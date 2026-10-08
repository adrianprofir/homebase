# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the **Security** tab of the repository and choose **Report a vulnerability**.
Do not open a public issue for a security problem.

Include what you found, how to reproduce it, and what you think the impact is.
You can expect an acknowledgement within a few days.
This is a volunteer-maintained project, so fixes are made on a best-effort basis, and reporters are credited unless they prefer otherwise.

## Scope

homebase is a single-tenant tool designed for trusted networks such as a home LAN or a tailnet.
It is served over plain HTTP by default, and every logged-in user can see all inventory data.
Running it directly on the open internet is not a supported setup; put it behind a TLS-terminating reverse proxy and an access layer you trust if you need remote access.

These areas are the most security-relevant:

- Authentication and the login-required checks on every dashboard page.
- Handling of provider API tokens: they must never be logged, rendered in a page, or shown in a sync error.
- The provider sync, which must only ever send read requests to provider APIs.
- The app checker, which sends GET requests to the URLs entered in the inventory, including private addresses by design.

Reports that need an attacker to already hold admin access, or that rely on exposing the dashboard to the internet without TLS or access control, are out of scope.

## Supported versions

Only the latest release and the `main` branch receive fixes.
