# Keeping dependencies up to date, at night

## What this is and why it exists

Every program is built on other people's libraries ("dependencies"). Their authors keep fixing bugs and
security holes, so staying on old versions slowly becomes a risk. Updating by hand is easy to forget, and
doing it in the middle of a trading day is risky, because a merge into `main` deploys straight to the shops.

This pipeline does the routine part automatically and at a quiet hour:

1. **Dependabot** (a GitHub service) checks every Monday at 01:00 Nairobi time for newer versions and opens
   a pull request for them. It is set up by `.github/dependabot.yml`.
2. The normal **checks** run on that pull request, exactly as for any change.
3. Every night at **02:00 Nairobi time** the workflow `.github/workflows/night-dependency-updates.yml`
   looks at the open Dependabot pull requests and merges **one** that is safe. Then it starts the deploy,
   so the update reaches the shops while they are closed.

GitHub starts scheduled workflows on a best-effort basis, so on a busy day it can run up to about an hour
late. It will not run in the middle of a working day.

## What counts as safe

An update is merged only when **all** of these are true:

| Rule | Why |
| --- | --- |
| It was opened by Dependabot and is not a draft | A person's work is never touched. |
| Every check has finished and passed | A failing or still-running check means do not ship. |
| GitHub reports it as clean (no conflicts) | Avoids merging something that is out of date. |
| No package jumps to a new **major** version | A major version (for example 6 to 7) is where things break, so a person reads the release notes first. For `0.x` versions, a minor step (0.9 to 0.10) counts as major. |
| Its version changes can be read | If the pull request text cannot be understood, it is left for a person. |

Only **one** update is merged per night. If something goes wrong after an update, it is obvious which one
did it and it can be undone on its own.

Anything that is not safe is simply **left open** for you. It is listed, with the reason, in the run's
summary (see below).

## What you do

- **Normally nothing.** Safe updates arrive on their own.
- **Now and then**, open the repository's **Pull requests** tab and look for ones from `dependabot`. Those
  are major updates or ones with a failing check. Read the notes, and merge them yourself when you are
  happy (do it in the evening, or ask for the deploy to wait until night).
- **To see what last night did:** repository, **Actions**, **Night dependency updates**, open the latest
  run and read the summary at the bottom of the page.

## Controls

- **Switch automatic merging off:** repository, **Settings**, **Secrets and variables**, **Actions**,
  **Variables**, add a variable named `DEPENDENCY_AUTOMERGE` with the value `false`. The workflow then only
  reports what it would have done. Remove the variable (or set it to `true`) to turn merging back on.
- **Try it without changing anything:** **Actions**, **Night dependency updates**, **Run workflow**. The
  "dry run" box is ticked by default, so it reports and merges nothing.
- **Undo an update that caused trouble:** open the merged pull request and press **Revert**. That makes a
  new pull request that undoes it; merge it, and the deploy puts the old version back.

## Honest limits

- The checks catch a lot, but not everything. They cannot see a screen that looks wrong or a feature that
  is slightly different. That is why only small updates are automatic, one per night.
- GitHub pauses scheduled workflows in a repository that has had no activity for 60 days. If that ever
  happens, GitHub shows a banner on the Actions page with a button to turn it back on.
- Dependabot also raises **security** pull requests as soon as a vulnerable version is found, outside the
  Monday schedule. Safe ones are shipped the same night; major ones wait for you.
- The rules live in `.github/scripts/merge_dependency_updates.py` and are tested by
  `.github/scripts/test_merge_dependency_updates.py`, which runs on every pull request.
