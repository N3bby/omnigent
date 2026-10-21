"""Only a repository's Git server through the exit node: images/runner/tailscale/exit-node-routes.sh.

Runs the script with a fake ``ip``, which keeps its rules in a file, and a
fake ``getent``, so it never touches the machine's own routing.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "images" / "runner" / "tailscale" / "exit-node-routes.sh"

# `ip -4|-6 rule add|del|show priority N [to A] [lookup T [suppress_prefixlength 0] | unreachable]`
FAKE_IP = r'''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_IP_STATE"]
rules = json.load(open(state)) if os.path.exists(state) else []
family, _, verb, *rest = sys.argv[1:]
priority = rest[1]
spec = " ".join(rest[2:])
if verb == "add":
    rules.append([family, priority, spec])
elif verb == "del":
    match = [r for r in rules if r[:2] == [family, priority] and (not spec or r[2] == spec)]
    if not match:
        sys.exit(2)
    rules.remove(match[0])
else:
    for r in rules:
        if r[:2] == [family, priority]:
            print(f"{priority}:\tfrom all {r[2]}")
json.dump(rules, open(state, "w"))
'''

FAKE_GETENT = r'''#!/usr/bin/env python3
import json, os, sys
addresses = json.loads(os.environ["FAKE_HOSTS"]).get(sys.argv[2])
if not addresses:
    sys.exit(2)
for address in addresses:
    for kind in ("STREAM", "DGRAM", "RAW"):
        print(f"{address:<15} {kind} {sys.argv[2]}")
'''


class ExitNodeRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name, text in (("ip", FAKE_IP), ("getent", FAKE_GETENT)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        self.hosts = self.tmp / "hosts"
        self.state = self.tmp / "rules.json"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_IP_STATE": str(self.state),
            "OMNIGENT_EXIT_NODE_HOSTS_FILE": str(self.hosts),
        }

    def run_script(self, hosts: dict[str, list[str]]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT)], env={**self.env, "FAKE_HOSTS": json.dumps(hosts)},
            capture_output=True, text=True, timeout=30,
        )

    def rules(self) -> list[tuple[str, str, str]]:
        return sorted(tuple(rule) for rule in json.loads(self.state.read_text()))

    def test_only_the_git_servers_addresses_go_through_the_exit_node(self) -> None:
        self.hosts.write_text("gitlab.cronos.example\n\n# a comment\n")
        hosts = {"gitlab.cronos.example": ["203.0.113.5", "2001:db8::5"]}
        result = self.run_script(hosts)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("203.0.113.5 goes through the exit node", result.stderr)
        expected = sorted([
            (family, priority, spec)
            for family in ("-4", "-6")
            for priority, spec in (
                ("5260", "lookup 52 suppress_prefixlength 0"),
                ("5262", "lookup main"),
                ("5263", "unreachable"),
            )
        ] + [
            ("-4", "5261", "to 203.0.113.5 lookup 52"),
            ("-6", "5261", "to 2001:db8::5 lookup 52"),
        ])
        self.assertEqual(self.rules(), expected)
        # Running it again adds no copies.
        self.assertEqual(self.run_script(hosts).returncode, 0)
        self.assertEqual(self.rules(), expected)

    def test_new_addresses_replace_old_ones_unless_a_host_does_not_resolve(self) -> None:
        self.hosts.write_text("gitlab.cronos.example\nregistry.cronos.example\n")
        self.run_script({"gitlab.cronos.example": ["203.0.113.5"], "registry.cronos.example": ["203.0.113.9"]})
        # The registry doesn't resolve: its address stays, and the new one is added.
        result = self.run_script({"gitlab.cronos.example": ["203.0.113.6"]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not resolve registry.cronos.example", result.stderr)
        routed = [spec for family, priority, spec in self.rules() if priority == "5261"]
        self.assertEqual(sorted(routed), [
            "to 203.0.113.5 lookup 52", "to 203.0.113.6 lookup 52", "to 203.0.113.9 lookup 52",
        ])
        # Once every host resolves, the old addresses go.
        result = self.run_script({"gitlab.cronos.example": ["203.0.113.6"], "registry.cronos.example": ["203.0.113.9"]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("203.0.113.5 no longer goes through the exit node", result.stderr)
        routed = [spec for family, priority, spec in self.rules() if priority == "5261"]
        self.assertEqual(sorted(routed), ["to 203.0.113.6 lookup 52", "to 203.0.113.9 lookup 52"])

    def test_the_tailscale_service_routes_only_git_servers_and_the_image_has_the_script(self) -> None:
        service = (ROOT / "images" / "runner" / "tailscale" / "tailscale-service.sh").read_text()
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("tailscale/exit-node-routes.sh /usr/local/bin/omnigent-exit-node-routes", dockerfile)
        self.assertIn('"$dir/tailscale-exit-node-git-only"', service)
        self.assertIn("hosts_file=/run/omnigent-tailscale.exit-node-hosts", service)
        script = SCRIPT.read_text()
        self.assertIn("hosts_file=${OMNIGENT_EXIT_NODE_HOSTS_FILE:-/run/omnigent-tailscale.exit-node-hosts}", script)
        # The rules go in before the exit node is set.
        self.assertLess(service.index("            omnigent-exit-node-routes\n"),
                        service.index('ts set --exit-node="$exit_nodes"'))


if __name__ == "__main__":
    unittest.main()
