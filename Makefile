SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
ENV ?= production
INVENTORY ?= ansible/inventory/$(ENV)/hosts.yml
ANSIBLE ?= $(if $(wildcard .venv/bin/ansible-playbook),.venv/bin/ansible-playbook,ansible-playbook)

.PHONY: help bootstrap check render diff deploy status smoke lock-images credential-status setup-codex setup-claude setup-git-token test

help:
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Install the pinned operator dependency into an ignored virtualenv
	python3 -m venv .venv
	.venv/bin/pip install --disable-pip-version-check -r requirements.txt

check: ## Validate configuration, manifests, scripts, and Ansible syntax
	./scripts/preflight --environment $(ENV) --inventory $(INVENTORY)

render: ## Render the complete non-secret Kubernetes desired state
	./scripts/render --environment $(ENV)

diff: render ## Show the server-side Kubernetes diff without changing the server
	$(ANSIBLE) -i $(INVENTORY) ansible/diff.yml -e omnigent_environment=$(ENV)

deploy: check render ## Provision/reconcile the VM and deploy; production is never automatic
	$(ANSIBLE) -i $(INVENTORY) ansible/site.yml -e omnigent_environment=$(ENV)

status: ## Inspect health, capacity, TLS, storage, and runner failures remotely
	$(ANSIBLE) -i $(INVENTORY) ansible/status.yml -e omnigent_environment=$(ENV)

smoke: ## Run the remote HTTPS and in-cluster health smoke tests
	$(ANSIBLE) -i $(INVENTORY) ansible/smoke.yml -e omnigent_environment=$(ENV)

lock-images: ## Resolve immutable GHCR digests for the configured release tags
	./scripts/lock-images --environment $(ENV)

credential-status: ## Check credential presence/status without printing values
	./scripts/remote-helper $(ENV) credential-status

setup-codex: ## Run the interactive Codex device OAuth flow on the server
	./scripts/remote-helper $(ENV) setup-codex

setup-claude: ## Store/rotate the shared Claude subscription token
	./scripts/remote-helper $(ENV) setup-claude

setup-git-token: ## Store/rotate the optional private-Git HTTPS token
	./scripts/remote-helper $(ENV) setup-git-token

test: check ## Run repository tests
	python3 -m unittest discover -s tests -v
