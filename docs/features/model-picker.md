# Model picker straight away

You can choose a model as soon as you open Omnigent, without starting a
session first.

![How the model list is remembered](../images/model-picker.svg)

## Why this is needed

Upstream Omnigent only learns which models exist once a runner has started.
With subscription logins, the model picker says "Models unavailable" until
then.

This deployment remembers the list that runners last reported, and shows it
straight away. The runner still checks the chosen model when it starts.

## Good to know

- **On a brand-new install the list is empty** until the first session has
  run once.

## Turning it off

You can't.

## How it works

The server patch
`images/server/patches/0001-sandbox-model-catalog-fallback.patch` saves the
list to the artifacts volume. The path is set by
`OMNIGENT_SANDBOX_CATALOG_FALLBACK_PATH` in `scripts/render`.
