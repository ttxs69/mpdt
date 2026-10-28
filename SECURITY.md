# Security policy

## What this tool handles

`mpdt` talks to a WeChat Official Account backend using cookies you provide. It
reads article and account statistics. It does not post, publish, or change
anything on the account.

The credentials it uses are as sensitive as a password: a WeChat backend cookie
grants full account access. **Never paste a cookie into a GitHub issue.**

## Reporting a vulnerability

Open a [private security advisory](https://github.com/ttxs69/mpdt/security/advisories/new).
That route is private; a normal issue is not.

Please include what you did, what happened, and — if the bug involves data
leaving the machine — which host it went to. Do not include a live cookie or a
real account's exported data.

This is a single-maintainer project, so there is no formal response SLA. Expect
an acknowledgement within a week.

## What is out of scope

- Anything that requires an already-compromised machine.
- The WeChat backend's own behaviour. This tool reports what the API returns.
- Rate limits, captchas, or account bans arising from polling frequency. The
  tool sleeps between requests; running it in a tight loop is your choice.

## Supported versions

Only the latest release on PyPI receives fixes. The version is pinned in
`pyproject.toml` and `mpdt/__init__.py`; see `scripts/release.sh`.
