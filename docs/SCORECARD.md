# Evidence scorecard

Generated from the versioned case registry and validated per-case results.
Do not edit this view. No product verdict or omnibus quality score is computed.
NOT RUN is not a hardware PASS. Prepared fixtures are not rendered signal coverage.

Inspected partition: **development**. Other partitions were not read.
Sealed holdout allocations carry no inferred measurement outcome.

integrated-rtl: 0 measured cases in the inspected partition; no measured evidence is registered here.
board: 0 measured cases in the inspected partition; no measured evidence is registered here.
silicon: 0 measured cases in the inspected partition; no measured evidence is registered here.

| Partition | Allocated | Inspected | Sealed | Required rows | PASS | FAIL | NO VERDICT | NOT RUN | STALE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| development | 488 | 488 | 0 | 33672 | 0 | 0 | 0 | 488 | 0 |
| holdout | 32 | 0 | 32 | 2208 | 0 | 0 | 0 | 0 | 0 |

Raw row verdicts remain in JSON, including MISSING EVIDENCE. Refusals have null observations.
Synthetic controls are excluded from actual Voice and hardware measurement counts.

| Required trace | Property | Unit | Estimator | Rubric |
| --- | --- | --- | --- | --- |
| adsr_1.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| adsr_1.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| adsr_2.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| adsr_2.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.noise_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.noise_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_1_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_1_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_1_pitch | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_1_pitch | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_2_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_2_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_2_pitch | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| control_upsample.vco_2_pitch | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| keyboard.duration | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| keyboard.duration | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| keyboard.midi_f0 | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| keyboard.midi_f0 | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1.post_control_vca | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1.post_control_vca | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1.raw | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1.raw | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1_amp_adsr.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1_amp_adsr.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1_rate_adsr.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_1_rate_adsr.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2.post_control_vca | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2.post_control_vca | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2.raw | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2.raw | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2_amp_adsr.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2_amp_adsr.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2_rate_adsr.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| lfo_2_rate_adsr.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.gain | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.gain | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.peak | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.peak | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.pre_normalization | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.pre_normalization | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.noise_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.noise_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_1_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_1_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_1_pitch | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_1_pitch | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_2_amp | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_2_amp | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_2_pitch | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mod_matrix.vco_2_pitch | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| noise.post_vca | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| noise.post_vca | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| noise.raw | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| noise.raw | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_1.post_vca | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_1.post_vca | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_1.raw | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_1.raw | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_2.post_vca | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_2.post_vca | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_2.raw | framing_match | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| vco_2.raw | exact_equal | 1 | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | mean_error | amplitude | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | error_rms | amplitude | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | max_abs_error | amplitude | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | sample_count_delta | sample | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |
| mixer.output | snr_db | dB | time-locked-paired@pending-qualification | voice-comparison-unqualified@1 |

| Case | Family | Partition | Outcome / access | Present / required rows | Reason |
| --- | --- | --- | --- | --- | --- |
| boundary:adsr_1.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_1.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:adsr_2.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.duration:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.duration:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.duration:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.duration:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.midi_f0:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.midi_f0:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.midi_f0:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:keyboard.midi_f0:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.frequency:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.frequency:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.frequency:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.frequency:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.initial_phase:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.initial_phase:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.initial_phase:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.initial_phase:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:center | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:center-above | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:center-below | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.mod_depth:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.rsaw:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.rsaw:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.rsaw:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.rsaw:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.saw:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.saw:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.saw:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.saw:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sin:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sin:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sin:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sin:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sqr:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sqr:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sqr:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.sqr:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.tri:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.tri:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.tri:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1.tri:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_amp_adsr.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_1_rate_adsr.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.frequency:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.frequency:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.frequency:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.frequency:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.initial_phase:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.initial_phase:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.initial_phase:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.initial_phase:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:center | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:center-above | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:center-below | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.mod_depth:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.rsaw:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.rsaw:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.rsaw:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.rsaw:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.saw:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.saw:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.saw:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.saw:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sin:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sin:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sin:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sin:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sqr:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sqr:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sqr:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.sqr:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.tri:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.tri:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.tri:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2.tri:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_amp_adsr.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.alpha:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.alpha:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.alpha:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.alpha:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.attack:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.attack:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.attack:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.attack:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.decay:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.decay:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.decay:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.decay:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.release:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.release:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.release:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.release:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.sustain:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.sustain:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.sustain:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:lfo_2_rate_adsr.sustain:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.noise:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.noise:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.noise:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.noise:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_1:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_1:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_1:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_1:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_2:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_2:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_2:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mixer.vco_2:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;noise_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;noise_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;noise_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;noise_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_1_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_1-&gt;vco_2_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;noise_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;noise_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;noise_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;noise_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_1_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.adsr_2-&gt;vco_2_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;noise_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;noise_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;noise_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;noise_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_1_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_1-&gt;vco_2_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;noise_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;noise_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;noise_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;noise_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_1_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_amp:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_amp:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_amp:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_amp:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_pitch:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_pitch:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_pitch:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:mod_matrix.lfo_2-&gt;vco_2_pitch:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.initial_phase:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.initial_phase:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.initial_phase:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.initial_phase:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:center | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:center-above | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:center-below | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.mod_depth:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.tuning:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.tuning:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.tuning:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_1.tuning:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.initial_phase:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.initial_phase:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.initial_phase:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.initial_phase:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:center | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:center-above | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:center-below | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.mod_depth:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.shape:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.shape:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.shape:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.shape:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.tuning:lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.tuning:near-lower | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.tuning:near-upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| boundary:vco_2.tuning:upper | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_1:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_1:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_1:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_1:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_2:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_2:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_2:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:adsr_2:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_amp_adsr:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_amp_adsr:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_amp_adsr:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_amp_adsr:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_rate_adsr:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_rate_adsr:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_rate_adsr:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_1_rate_adsr:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_amp_adsr:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_amp_adsr:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_amp_adsr:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_amp_adsr:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_rate_adsr:cut-attack | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_rate_adsr:cut-decay | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_rate_adsr:long-release | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| envelope:lfo_2_rate_adsr:zero-stages | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| normalization:above | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| normalization:below | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| normalization:tie | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_1-&gt;noise_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_1-&gt;vco_1_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_1-&gt;vco_1_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_1-&gt;vco_2_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_1-&gt;vco_2_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_2-&gt;noise_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_2-&gt;vco_1_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_2-&gt;vco_1_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_2-&gt;vco_2_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.adsr_2-&gt;vco_2_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_1-&gt;noise_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_1-&gt;vco_1_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_1-&gt;vco_1_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_1-&gt;vco_2_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_1-&gt;vco_2_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_2-&gt;noise_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_2-&gt;vco_1_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_2-&gt;vco_1_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_2-&gt;vco_2_amp | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| route:mod_matrix.lfo_2-&gt;vco_2_pitch | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| source:noise | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| source:vco_1 | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| source:vco_2 | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| special:near-silence | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| special:silence | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| special:stress | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:blend | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:rsaw | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:saw | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:sin | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:sqr | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_1:tri | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:blend | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:rsaw | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:saw | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:sin | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:sqr | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:lfo_2:tri | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:vco_2:blend | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:vco_2:saw | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| waveform:vco_2:square | directed | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000000 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000001 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000002 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000003 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000004 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000005 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000006 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000007 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000008 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000009 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000010 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000011 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000012 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000013 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000014 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000015 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000016 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000017 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000018 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000019 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000020 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000021 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000022 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000023 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000024 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000025 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000026 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000027 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000028 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000029 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000030 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000031 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000032 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000033 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000034 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000035 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000036 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000037 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000038 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000039 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000040 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000041 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000042 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000043 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000044 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000045 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000046 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000047 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000048 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000049 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000050 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000051 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000052 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000053 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000054 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000055 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000056 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000057 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000058 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000059 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000060 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000061 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000062 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000063 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000064 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000065 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000066 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000067 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000068 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000069 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000070 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000071 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000072 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000073 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000074 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000075 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000076 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000077 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000078 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000079 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000080 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000081 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000082 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000083 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000084 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000085 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000086 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000087 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000088 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000089 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000090 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000091 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000092 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000093 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000094 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000095 | random | development | NOT RUN | 0 / 69 | no_attempt_directory |
| random-000096 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000097 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000098 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000099 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000100 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000101 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000102 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000103 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000104 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000105 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000106 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000107 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000108 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000109 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000110 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000111 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000112 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000113 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000114 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000115 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000116 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000117 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000118 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000119 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000120 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000121 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000122 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000123 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000124 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000125 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000126 | random | holdout | sealed | ? / 69 | partition_not_inspected |
| random-000127 | random | holdout | sealed | ? / 69 | partition_not_inspected |
