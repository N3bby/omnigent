"""How the hermes-remote harness reaches Hermes: its SSH settings and commands.

Tests packages/omnigent-hermes-remote's connection.py on its own; it imports
nothing from Omnigent. The tunnel itself needs a real SSH server, so it's
checked by hand (see docs/features/hermes-remote.md).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0, str(ROOT / "packages" / "omnigent-hermes-remote" / "omnigent" / "community" / "harness")
)
from hermes_remote import connection  # noqa: E402
from hermes_remote.client import HermesError  # noqa: E402

KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"


class SettingsTest(unittest.TestCase):
    def test_host_user_port_and_key(self) -> None:
        settings = connection.Settings.from_env(
            {
                "HERMES_REMOTE_SSH_HOST": "home-hermes",
                "HERMES_REMOTE_SSH_USER": "n3bby",
                "HERMES_REMOTE_SSH_PORT": "2222",
                "HERMES_REMOTE_SSH_KEY": KEY,
            }
        )

        self.assertEqual(settings.target, "n3bby@home-hermes")
        self.assertEqual(settings.port, 2222)
        # OpenSSH refuses a key file without its final newline.
        self.assertEqual(settings.private_key, KEY + "\n")
        self.assertEqual(settings.remote_env_file, "~/.config/hermes/api-server.env")

    def test_only_the_host_is_required(self) -> None:
        settings = connection.Settings.from_env({"HERMES_REMOTE_SSH_HOST": "home-hermes"})

        self.assertEqual(
            (settings.target, settings.port, settings.private_key), ("home-hermes", None, None)
        )

    def test_no_host_says_what_to_set(self) -> None:
        with self.assertRaisesRegex(HermesError, "HERMES_REMOTE_SSH_HOST"):
            connection.Settings.from_env({})

    def test_values_that_would_read_as_ssh_options_are_refused(self) -> None:
        for name, value in (
            ("HERMES_REMOTE_SSH_HOST", "-oProxyCommand=sh"),
            ("HERMES_REMOTE_SSH_USER", "-x"),
            ("HERMES_REMOTE_SSH_PORT", "22; rm"),
            ("HERMES_REMOTE_SSH_PORT", "70000"),
        ):
            with self.subTest(name=name, value=value):
                env = {"HERMES_REMOTE_SSH_HOST": "home-hermes", name: value}
                with self.assertRaises(HermesError):
                    connection.Settings.from_env(env)


class SshCommandTest(unittest.TestCase):
    def test_the_key_port_and_options_come_before_the_target(self) -> None:
        settings = connection.Settings(host="home-hermes", user="n3bby", port=2222)

        command = connection.ssh_command(
            settings, Path("/run/key"), "-N", "-L", "/s.sock:127.0.0.1:8642"
        )

        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[command.index("-p") + 1], "2222")
        self.assertEqual(command[command.index("-i") + 1], "/run/key")
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertEqual(command[-4:], ["-N", "-L", "/s.sock:127.0.0.1:8642", "n3bby@home-hermes"])

    def test_a_remote_command_comes_last(self) -> None:
        settings = connection.Settings(host="home-hermes")

        command = connection.ssh_command(settings, None, remote_command="cat ~/x")

        self.assertEqual(command[-2:], ["home-hermes", "cat ~/x"])
        self.assertNotIn("-i", command)
        self.assertNotIn("-p", command)

    def test_the_env_file_path_is_quoted_but_home_expands(self) -> None:
        self.assertEqual(
            connection.read_env_file_command("~/.config/hermes/api-server.env"),
            "cat ~/.config/hermes/api-server.env",
        )
        self.assertEqual(
            connection.read_env_file_command("/etc/my hermes.env"), "cat '/etc/my hermes.env'"
        )


if __name__ == "__main__":
    unittest.main()
