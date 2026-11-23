# Timestamps

Two records fix the study in time before any result existed. Each is a short text file naming a git commit and
the SHA-256 of a document, with an [OpenTimestamps](https://opentimestamps.org) proof (`.ots`) that the text file
existed when the proof was anchored in the Bitcoin blockchain.

| Record | Commits to | Proof |
|---|---|---|
| [`prereg-v1.txt`](prereg-v1.txt) | the registering commit and `docs/preregistration.md` | [`prereg-v1.txt.ots`](prereg-v1.txt.ots) |
| [`addendum-v1.txt`](addendum-v1.txt) | the commit that froze the scoring code and `docs/preregistration_addendum.md` | [`addendum-v1.txt.ots`](addendum-v1.txt.ots) |

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
