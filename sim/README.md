# Evidence records

Committed evidence is append-only. Each record must identify the exact source,
configuration, environment, commands, result artifacts, and limitations.

`reference/` may contain narrowly scoped software-reference qualification
records. These do not imply an RTL or ASIC result.

`evidence/` holds committed RTL/testbench evidence records produced by
`tb/run_oneshot.py` / `tb/run_voice.py` (issue #79) and verified committable
by `tools/verify_oneshot_evidence.py --require-reachable` before being added
here: `result: PASS`, a clean tree (`identity.git_tree_dirty: false`), an exact
`identity.git_head` citation, and that commit actually reachable on the default
branch. That last condition is why `--require-reachable` exists and why it is
not optional for a record being committed: every other check is satisfied by a
pristine record produced on a pull-request branch, and this repository
squash-merges, so such a record cites a SHA that resolves for nobody.

A record here is evidence for the **named profile** of its named lane only --
its filename is `oneshot-<lane>-<profile>-v1.json`, and
`tests/test_committed_oneshot_evidence.py` refuses a filename that disagrees
with the `profile` inside. The profiles of one lane are nested
(`regression` < `directed` < `full`), so a stronger record supersedes a weaker
one; that same suite refuses a stronger record that covers fewer cases or
demonstrates fewer negative controls than the weaker one it claims to
supersede. A `full`-profile record exists for the **tail-chain** lane only
(`oneshot-tail-chain-full-v1.json`); the whole-voice lane has none, so no
`full`-profile claim may be made for the integrated top. See
`spec/ONESHOT-E2E.md` for what each one does and does not establish, and for
the structural reason a record can only be committed on an already-existing,
clean, already-landed commit -- never inside the pull request that introduces
the RTL/flow it certifies.
