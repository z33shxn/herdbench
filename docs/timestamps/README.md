# Timestamps

Two records fix the study in time before any result existed. Each is a short text file naming a git commit and
the SHA-256 of a document, with an [OpenTimestamps](https://opentimestamps.org) proof (`.ots`) that the text file
existed when the proof was anchored in the Bitcoin blockchain.

| Record | Commits to | Proof |
|---|---|---|
| [`prereg-v1.txt`](prereg-v1.txt) | the registering commit and `docs/preregistration.md` | [`prereg-v1.txt.ots`](prereg-v1.txt.ots) |
| [`addendum-v1.txt`](addendum-v1.txt) | the commit that froze the scoring code and `docs/preregistration_addendum.md` | [`addendum-v1.txt.ots`](addendum-v1.txt.ots) |

## What the proofs show

| Record | Submitted to the calendars (by the git commit time) | First Bitcoin anchor |
|---|---|---|
| `prereg-v1.txt` | 2026-11-17, 21:43 UTC (just after the registering commit) | block 976,330, mined 2026-11-17 22:07 UTC; also blocks 976,331, 976,355 and 976,378 |
| `addendum-v1.txt` | 2026-11-23, 14:51 UTC (the commit time of `e891b76`) | block 977,225, mined 2026-11-23 14:59 UTC |

The pre-registration, which fixes every registered analysis choice, is anchored five days before any proxy was
compared with the truth. The addendum records facts about the data (the entity map's hash, coverage, suppression and the gates), not
analysis choices. Its Bitcoin anchor (block 977,225, mined 14:59:27 UTC) came seven and a half minutes after the
scoring run started (14:51:57 UTC by the run log), so the anchor alone bounds when the addendum existed but does
not order it before scoring. That order rests on the git history (`a77b84d` and `e891b76` precede `42f6fa8`,
which adds the results), whose times the author's machine set.

## Typography since registration

On 24 November 2026 (version 1.1.0) the repository adopted plain hyphens everywhere: en dashes and Unicode minus
signs became ASCII hyphens in the text (the generated report also writes `n/a` for an empty cell and `[low, high]`
for a rank range). That includes the working-tree copy of `docs/preregistration.md`, whose 6 en dashes and 13
minus signs became hyphens and nothing else changed. The addendum held none and is untouched, and the records
and proofs here are unchanged. The hashes in the records are of the documents as registered, which git keeps at the
recorded commits. `tests/test_preregistration.py` checks both hashes and that each working-tree file differs from
its registered version only in those two characters.

## Verify

1. Check the registered documents against the records. Hash each document as it stood at its recorded commit; the
   results must equal the values in the two text files:

   ```bash
   git show f205501:docs/preregistration.md | sha256sum            # ae32842f15e2...
   git show a77b84d:docs/preregistration_addendum.md | sha256sum   # 5cf5cbb72965...
   ```

   The working-tree addendum hashes to the same value. For the pre-registration, confirm that the working tree
   differs only in typography: `git diff --word-diff=color f205501 -- docs/preregistration.md` shows nothing but
   en dashes and minus signs turned into hyphens. Text files are stored with LF line ends (`.gitattributes`), so the hashes are the
   same on every platform.
2. Check each proof, either way:
   * drop the `.ots` file and its text file on <https://opentimestamps.org>, or
   * run `ots verify docs/timestamps/prereg-v1.txt.ots` with the
     [OpenTimestamps client](https://github.com/opentimestamps/opentimestamps-client) and a Bitcoin node.

A proof shows the latest time by which the text file existed: the time of the Bitcoin block it is anchored in.
