# Reserva Comum — version 3.0

A local shared-space reservation application developed by Team 5 for PSI / IFCE.
Built with React 18, CSS, Python 3.10+ (standard library only), and SQLite.

The application includes username/password authentication, role-based access,
account administration, RF15 reports, availability browsing, reservation requests,
manager decisions, cancellation, incident records, schedule blocks, and audit history.
The interface is in Brazilian Portuguese; the labels below match the application.

## Quick start

From the project root, create an administrator:

```bash
python3 server.py --create-admin
```

Enter a username and confirm the password. Password input is hidden.
Passwords must contain 10–128 characters. Usernames must contain 3–32 characters:
ASCII letters, digits, underscores, periods, or hyphens. The first character must
be a letter, digit, or underscore. Usernames are normalized to lowercase.
No accounts or default passwords are provisioned automatically.

Start the application:

```bash
python3 server.py
```

Open **http://localhost:8000**, sign in, and open **Contas** to create user and manager
accounts. On Windows, replace `python3` with `py -3`. After creating the first admin,
`iniciar_windows.bat` can also start the application.

## Roles and permissions

| Role | View availability | Request/cancel own reservations | Review requests, manage incidents and blocks | View reports / CSV | Manage accounts |
|---|---|---|---|---|---|
| `usuario` | Yes | Yes | No | No | No |
| `gestor` | Yes | No | Yes | Yes | No |
| `admin` | Yes | No | Yes | Yes | Yes |

Roles are stored in the database and checked by the server. They cannot be selected
at sign-in or overridden by client input. Sign out with **Sair** before testing another
account. Managers and admins see an expanded agenda without a reservation form.

Admins create accounts, change roles, activate/deactivate accounts, and reset
passwords. Role, account-status, and password changes revoke the account's sessions.
The last active administrator cannot be deactivated or demoted. An admin cannot
change their own role/status through the account administration form.

All accounts can change their own password under **Minha conta**. A password change
requires the current password and signs out all sessions for that account.

## Existing data and upgrades

The default database is `reservas.db`. Schema changes are applied automatically on
startup without deleting existing reservations or accounts. Stop the server and
back up the database before upgrading; restart it after backend changes.

Legacy reservations retain their usernames. Creating an account with a matching
username, such as `morador`, grants access to that username's reservation history;
admins should assign those names only to the correct person. Legacy demonstration
profiles do not receive passwords automatically. Other records remain visible to
management without granting access to someone who does not have a valid account.

Legacy incidents without a timestamp remain undated. They are listed separately in
CSV reports rather than being assigned to an invented date. New incidents receive
their registration timestamp automatically. Legacy blocks are also preserved,
even when their original creation event is unavailable.

A new database includes two spaces and fictional examples: a barbecue reservation
and a pool-cleaning block scheduled three days after the first startup. These dates
do not move on subsequent restarts. Use future dates when rehearsing a demonstration.

For a separate rehearsal database:

```bash
RESERVA_DB=rehearsal.db python3 server.py --create-admin
RESERVA_DB=rehearsal.db python3 server.py
```

Use the same `RESERVA_DB` value for both commands. In PowerShell, set
`$env:RESERVA_DB="rehearsal.db"` before running the Python commands.

## Reservation rules

| Rule | Barbecue area (`Churrasqueira`) | Pool (`Piscina`) |
|---|---|---|
| Capacity, including the requester | 20 people | 12 people |
| Opening hours | 08:00–22:00 | 08:00–20:00 |
| Minimum advance notice | 24 hours | 2 hours |
| Active reservations per account / month | 3 | 4 |
| Informational fee | BRL 50 | Free |
| Manager approval required | Yes | No |

These are fictional project parameters. Business timestamps use Fortaleza time
(UTC−3). Pool reservations are automatically confirmed when all rules pass;
barbecue requests remain pending until a manager or admin makes a decision.

Pending and confirmed reservations both occupy their intervals. Creation and
approval revalidate conflicts and current impediments inside a SQLite write
transaction. Advance notice is checked when submitting the request; manager
approval is allowed until the reservation starts without requiring the original
advance-notice window a second time. Requesters may cancel
before the reservation starts. Cancellation and slot release are atomic; repeated
cancellation does not duplicate its audit event. Reservation audit records cannot
be updated or deleted through SQL while the protection triggers are installed.

Manager decisions require a justification of 5–200 characters. Leading/trailing
spaces do not count toward the minimum. The live counter turns red below the
minimum or above the maximum, and decision buttons appear unavailable while the
text is invalid. Clicking an unavailable decision explains the applicable limit
without submitting it. Both approval and rejection are validated again by the API.
Server refusals and their failed rules appear beside the decision buttons as well
as in the page notice, so a blocked approval does not appear to do nothing.

On the user agenda, click the first and last hourly cells to select an interval.
Selected cells are blue, separate from confirmed and pending states. Start/end
fields also update the highlight. The application opens on today's date in
Fortaleza. The agenda date picker and the **Data da reserva** field under
**Solicitar reserva** share the same selected date. Date chevrons move one day at
a time. The agenda picker can browse past dates in read-only mode, showing stored
reservation statuses and effective blocked intervals without exposing third-party
personal data. Cancelled/rejected reservations remain visible in the historical
list. The two-column layout stays unchanged when browsing past dates: the request
or blocking form remains visible, faded, and disabled. Confirmed reservations are
labelled **Reservada** for other viewers, including managers/admins; only the
requesting user sees **Confirmada** on their own agenda and historical entries.
The reservation date field remains limited to today or later. Browsing a past day
does not allow creating reservations or blocks and does not generate data.

The agenda clock is synchronized with the server and updates unavailable cells
as time passes. Past starts and reservation times that do not meet the space's
advance notice are disabled; manager blocks require a future start. Manual time
fields and submission controls follow those same limits. Pool reservations still
require two hours' notice, barbecue reservations still require 24 hours, and no
late-booking grace period or new approval-expiration rule is applied. The backend
remains the authority for availability and validates every write again.

## Creating spaces

Managers and admins can use **Cadastrar espaço** to register a name, description,
capacity, opening/closing hours, informational fee, minimum advance notice,
monthly reservation limit, and whether manager approval is required. The new
space becomes available in the existing agenda, booking, blocking, and reporting
flows. Regular users cannot create spaces.

These parameters are already persisted in `spaces` and read by the generic
reservation validator. The pool/barbecue values in `init()` are initial examples,
not conditions that limit the application to two spaces. Core algorithms such as
conflict checks, ownership, suspension, and same-day intervals remain implemented
in code. New spaces use whole-hour opening/closing times between 00:00 and 23:00,
without crossing midnight; names must be unique ignoring case. This screen creates
spaces; editing existing rules and spaces remains outside this update.

## Schedule blocks

Under **Espaços e agenda**, managers and admins select a space and date, then use
**Bloquear horários** beside the agenda on desktop or below it on narrow screens.
Click the first and last available cells to fill the blocking interval, or enter
start/end times directly. The blue selection stays synchronized with the form;
confirmed, pending, and blocked cells retain their own colors and are unavailable
for selection. Saving a block refreshes the agenda and clears the temporary selection.

Changing the start suggests an end one hour later, capped at the space's closing
time. The end remains editable for longer intervals. Enter a 5–500-character
reason; the reason is visible in the public agenda.

New blocks must start in the future, stay within one day and the space's opening
hours, and avoid pending/confirmed reservations and other active blocks. Adjacent
intervals are allowed. Conflict checks and writes use the same transaction model
as reservations, preventing concurrent booking/block conflicts.

Under **Bloqueios deste dia**, management may remove a block with a justification.
The action releases availability and records the actor, timestamp, and reason in
an append-only block audit. Repeating removal does not duplicate the event.
Ended blocks are preserved. If a block is removed early, reports retain its elapsed
blocked hours and release only the remaining portion.

## Incidents and reports (RF15)

Under **Gestão e auditoria**, management can register an incident for a user and
space, including a description, action taken, and optional suspension end time.
Active suspensions prevent new reservations. Avoid unnecessary personal data.

Under **Relatórios**, managers and admins select a date range and optional space,
choose **Gerar relatório**, and use **Exportar CSV** to download the result.
The date range is inclusive and supports 1–366 days. Without explicit filters,
the API uses the current month through today.

Per-space indicators include:

- Reservation counts by current status, including cancellations and rejections.
- Confirmed hours and available opening hours.
- Occupancy: confirmed hours divided by available opening hours.
- Average time from request creation to manual approval, with sample count.
- Incidents registered during the period; undated records in a separate CSV column.

Reservations are selected by their scheduled usage interval and current status.
Cancellation counts therefore refer to reservations scheduled for the period,
not the date of cancellation. Occupancy deducts the union of blocked intervals
without double-counting overlaps. Pending requests do not count as confirmed hours.

Approval averages require both creation and manual-approval audit events, even if
the reservation was subsequently cancelled. Automatic confirmations and legacy
records missing those events are excluded rather than treated as zero-minute
samples. Confirmed hours represent reservations, not verified physical attendance.
Incidents are selected by their registration timestamp.

## Authentication and local operation

- Passwords use random salts and scrypt (`N=16384, r=8, p=5`), with constant-time
  digest comparison.
- Random session tokens are stored only as hashes, expire after eight hours, and
  are revoked on logout and relevant account/password changes.
- Cookies use `HttpOnly` and `SameSite=Strict`; authenticated writes require CSRF
  tokens, and POST requests validate the supplied origin.
- Sign-in attempts are rate-limited; access and account-administration events
  are recorded.
- Internal data, reports, and management operations are authorized by the server.

The server binds to `127.0.0.1` and uses HTTP for local execution. Deployment would
require an appropriate application server and HTTPS; enable
`RESERVA_COOKIE_SECURE=1` behind HTTPS. TLS, multiple organizations, email-based
password recovery, MFA, and automated backups are not implemented.

## Frontend development

The compiled frontend is tracked in `frontend/dist/`, allowing the application to
run without Node.js. To rebuild, use Node.js 22 and npm:

```bash
cd frontend
npm ci
npm run build
```

The build updates `dist/app.js` and `dist/app.css`. Keep `dist/index.html`.
Commit source and compiled-asset changes together. Dependency license notices
remain in the bundle and `LICENCAS_TERCEIROS.txt`.

## Tests

From the project root:

```bash
python3 -m unittest discover -v
```

Tests use temporary databases and accounts, preserving the application's local
runtime data. Coverage includes reservations, concurrency, authentication,
authorization, CSRF, session expiration/logout, account administration, password
changes, additive migrations, reports, schedule blocks, and block audit history.
Each test receives an isolated database.

If the environment prevents opening TCP ports, use the in-memory transport:

```bash
RESERVA_TEST_TRANSPORT=memory python3 -m unittest discover -v
```

This exercises the real HTTP parser, cookies, headers, and handlers without opening
a TCP port. It does not replace TCP integration checks or visual UI review.

## Continuous integration

[Repository](https://github.com/ulissesbon/Reserva-Comum)

`.github/workflows/ci.yml` runs on pushes, pull requests, and manual dispatch:

1. HTTP integration tests on Python 3.12 and 3.14.
2. `npm ci` and frontend compilation on Node.js 22.
3. Upload of the compiled frontend as a workflow artifact.

The workflow becomes available when its commit reaches GitHub. It does not deploy
the application. Runtime databases, `.env` files, virtual environments, and installed
dependencies are excluded by `.gitignore`.

For the GitHub Actions minicourse, use a dedicated teaching branch to demonstrate
success → capacity-validation failure → correction, keeping the main application
functional throughout the exercise.

## Version-control workflow

Use English for branch names, commit messages, pull-request titles/descriptions,
and technical documentation. Communicate with the project owner in Brazilian
Portuguese and retain the application's current UI language.

- Create a focused branch, such as `feat/schedule-blocks` or `fix/booking-conflicts`.
- Complete one logical change and run the appropriate checks.
- Review the diff and stage only that change, including related tests and compiled
  assets when relevant.
- Commit each completed change separately using Conventional Commits.
- Push when requested, review the GitHub Actions result, and integrate through a
  pull request.

Examples:

```text
feat: allow managers to block time slots
fix: prevent overlapping reservations
ci: add backend tests and frontend build workflow
docs: document setup and development workflow
```

The initial import records the application built before Git was configured.
Subsequent features, fixes, and independent adjustments should have separate,
small commits. Repository instructions are maintained in `AGENTS.md`.

## Project structure

- `server.py`: HTTP server, business rules, additive migrations, APIs, admin setup.
- `auth.py`: accounts, passwords, sessions, and security events.
- `reports.py`: report calculations and CSV export.
- `test_poc.py`: reservation regressions and HTTP test transports.
- `test_features.py`: authentication, permissions, and reports.
- `test_blocks.py`: schedule blocks, conflicts, concurrency, and audit history.
- `test_timing.py`: server clock, advance-notice boundaries, and past-time rejection.
- `test_decision_reason.py`: decision-reason boundaries and character counts.
- `test_history.py`: private historical agenda reads.
- `test_spaces.py`: configurable-space creation and rule validation.
- `frontend/src/`: React interface and CSS.
- `frontend/dist/`: compiled application ready for local execution.
- `.github/workflows/ci.yml`: continuous integration.
- `AGENTS.md`: contributor and coding-agent workflow instructions.

## Remaining scope

Waitlists, external notifications, editing existing spaces/rules through the UI,
payments, and financial reports are not implemented. Incident records link a user
and a space but do not include attachments or classification.

Automated tests are not a usability study or a certification of production
security, accessibility, or privacy compliance.
