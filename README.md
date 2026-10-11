### Murzak POS

Murzak POS

### Installation

This app needs a Frappe 15 bench with ERPNext 15 and the Kenya tax app (`kenya-compliance`) in it. Using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/frappe/erpnext.git --branch version-15
# Fetched by its full address: its real repository name has a hyphen, which "--resolve-deps" would not find
bench get-app https://github.com/navariltd/kenya-compliance.git
bench get-app $URL_OF_THIS_REPO --branch main
bench --site <site> install-app murzak_pos
```

To build a complete staging server (database included) in one go, see [docs/STAGING_SERVER.md](docs/STAGING_SERVER.md).

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/murzak_pos
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### Checks on every pull request

Every pull request into `main` runs two automatic checks (`.github/workflows/ci.yml`):

1. **Lint the changed files.** A new Python file must pass `ruff` cleanly and be formatted. An older file may not gain new `ruff` findings. Files must parse, JSON and YAML must be valid, and added lines may not end in spaces. Older files are not checked as a whole because they already have many findings.
2. **Unit tests with no database.** Runs every `test_*.py` that does not need a database, on a plain install of Frappe 15. It takes about a minute.

To run them yourself before pushing (from the repository root):

```bash
pip install ruff==0.8.1 pyyaml "frappe @ git+https://github.com/frappe/frappe.git@version-15"
pip install --no-deps "erpnext @ git+https://github.com/frappe/erpnext.git@version-15"
python .github/scripts/check_changed_files.py origin/main
python .github/scripts/run_unit_tests.py
```

A few tests need a full bench site (database and Redis) and are skipped by the second check; the list is `NEEDS_A_SITE` in `.github/scripts/run_unit_tests.py`. Run those on a staging server, one test file at a time, as described in [docs/STAGING_SERVER.md](docs/STAGING_SERVER.md).

### Server setup (once)

The website's own web server (Nginx) must send two protections that only a server can send: stopping other websites from showing the till inside their own page, and making browsers always use a secure connection. The steps, which take about 10 minutes, are in [docs/SERVER_SECURITY_SETUP.md](docs/SERVER_SECURITY_SETUP.md), with a checker (`murzak_pos/api/scripts/check_security_headers.py`) that confirms they worked.

### License

mit
