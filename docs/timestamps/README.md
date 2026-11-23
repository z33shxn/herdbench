# Timestamps

Two records fix the study in time before any result existed. Each is a short text file naming a git commit and
the SHA-256 of a document, with an [OpenTimestamps](https://opentimestamps.org) proof (`.ots`) that the text file
existed when the proof was anchored in the Bitcoin blockchain.

| Record | Commits to | Proof |
|---|---|---|
| [`prereg-v1.txt`](prereg-v1.txt) | the registering commit and `docs/preregistration.md` | [`prereg-v1.txt.ots`](prereg-v1.txt.ots) |
| [`addendum-v1.txt`](addendum-v1.txt) | the commit that froze the scoring code and `docs/preregistration_addendum.md` | [`addendum-v1.txt.ots`](addendum-v1.txt.ots) |

## What the proofs show

| Record | Submitted to the calendars | First Bitcoin anchor |
|---|---|---|
| `prereg-v1.txt` | 2026-11-17, 21:43 UTC (just after the registering commit) | block 976,330, mined 2026-11-17 22:07 UTC; also blocks 976,331, 976,355 and 976,378 |
| `addendum-v1.txt` | 2026-11-23, 14:51 UTC (the commit time of `e891b76`) | block 977,225, mined 2026-11-23 14:59 UTC |

The pre-registration, which fixes every analysis choice, is anchored five days before any proxy was compared with the
truth. The addendum records facts about the data (the entity map's hash, coverage, suppression and the gates), not
analysis choices. Its Bitcoin anchor came eight minutes after the scoring run started (14:51:57 UTC by the run log), so
the anchor alone bounds when the addendum existed but does not order it before scoring; that order rests on the git
history (`a77b84d` and `e891b76` precede `42f6fa8`, which adds the results) and on the calendars' receipt at 14:51 UTC.

## Verify

1. Check the documents against the records: the SHA-256 of `docs/preregistration.md` and of
   `docs/preregistration_addendum.md` must equal the values in the two text files, and each named commit must be in
   this repository's history (`git cat-file -t <commit>`). Text files are stored with LF line ends
   (`.gitattributes`), so the hashes are the same on every platform.
2. Check each proof, either way:
   * drop the `.ots` file and its text file on <https://opentimestamps.org>, or
   * run `ots verify docs/timestamps/prereg-v1.txt.ots` with the
     [OpenTimestamps client](https://github.com/opentimestamps/opentimestamps-client) and a Bitcoin node.

A proof shows the latest time by which the text file existed: the time of the Bitcoin block it is anchored in.
