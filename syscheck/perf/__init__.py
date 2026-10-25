"""Performance & hang judgement, kept free of Qt and WMI so it tests anywhere.

    stutter   -> gaps in a 10 ms timer loop: stutters and freezes
    events    -> event-log parsing, problem counts, recent-reinstall check
    system    -> power plan, startup programs, memory channels, GPU kind
    score     -> sub-scores, Performance score, label, typical-use guide
    hangs     -> "why it might hang", most likely cause first
    describe  -> the assessment and display rows the views and report share

The measurements themselves are taken by
:mod:`syscheck.telemetry.performance`.
"""
