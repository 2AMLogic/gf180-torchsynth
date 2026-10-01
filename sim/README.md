# Evidence records

Committed evidence is append-only. Each record must identify the exact source,
configuration, environment, commands, result artifacts, and limitations.

`reference/` may contain narrowly scoped software-reference qualification
records. These do not imply an RTL or ASIC result.

`evidence/` holds committed RTL/testbench evidence records produced by
`tb/run_oneshot.py` / `tb/run_voice.py` (issue #79) and verified committable
by `tools/verify_oneshot_evidence.py` before being added here: `result:
PASS`, a clean tree (`identity.git_tree_dirty: false`) and an exact
`identity.git_head` citation. A record here is evidence for the regression
(not `full`) profile of its named lane only; see `spec/ONESHOT-E2E.md` for
what each one does and does not establish, and for the structural reason a
record can only be committed on an already-existing, clean commit -- never
inside the pull request that introduces the RTL/flow it certifies.
