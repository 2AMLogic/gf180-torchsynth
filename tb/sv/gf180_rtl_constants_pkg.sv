package gf180_rtl_constants;
  // C1: Audio word 24-bit Q2.21 [-4, +4)
    localparam int C1_FRAC_BITS = 21;
    localparam int C1_INT_BITS = 2;
    localparam string C1_RANGE = "[-4, +4)";
    localparam int C1_SIGNED = 1;
    localparam int C1_WIDTH = 24;
  // C2: Phase: u32 wrapping, 2^32 units/turn
    localparam int C2_MODULAR = 1;
    localparam string C2_SEMANTICS = "wrapping";
    localparam int C2_SIGNED = 0;
    localparam int C2_UNITS_PER_TURN = 4294967296;
    localparam int C2_WIDTH = 32;
  // C3: Frequency word Q16.15
    localparam int C3_FRAC_BITS = 15;
    localparam int C3_INT_BITS = 16;
    localparam int C3_SIGNED = 1;
    localparam int C3_WIDTH = 32;
  // C4: MIDI domain Q10.21
    localparam int C4_FRAC_BITS = 21;
    localparam int C4_INT_BITS = 10;
    localparam int C4_SIGNED = 1;
    localparam int C4_WIDTH = 32;
  // C5: Sine: 4096x24 quarter-wave LUT + linear interp; 1K quadratic fallback pre-authorized
    localparam int C5_ENTRY_WIDTH = 24;
    localparam string C5_FALLBACK = "1024-entry quadratic interpolation, pre-authorized (DR-0008 Section 5)";
    localparam int C5_INDEX_BITS = 12;
    localparam int C5_INTERP_BITS = 18;
    localparam string C5_METHOD = "linear";
    localparam int C5_N_ENTRIES = 4096;
    localparam int C5_PHASE_BITS = 32;
  // C6: Rounding: half-even at S1-S5
    localparam string C6_ROUNDING_MODE = "half_even";
    localparam string C6_SITES = "S1,S2,S3,S4,S5";
  // C7: Saturation + sticky counters; Nyquist clamp forbidden
    localparam string C7_COUNTERS = "sticky per-site saturate/overflow";
    localparam string C7_NEVER_SATURATE_WORDS = "phase,frequency";
    localparam string C7_NYQUIST_CLAMP = "forbidden";
    localparam string C7_POLICY = "saturate";
  // C8: Noise: host-fed exact stream, slot sound_index % 32, seed 13
  // C9: Normalization replay gain 1/peak, declared-precision reciprocal
    localparam string C9_APPLICATION_SITE = "S5";
    localparam int C9_FRAC_BITS = 22;
    localparam string C9_GAIN_WORD = "U1.22";
    localparam int C9_INT_BITS = 1;
    localparam int C9_RECIPROCAL_FRAC_BITS = 22;
    localparam string C9_ROUNDING_MODE = "half_even";
    localparam int C9_SIGNED = 0;
    localparam int C9_WIDTH = 23;
  // C10: Thresholds: 2x-measured power-of-two, preregistered pre-freeze
endpackage
