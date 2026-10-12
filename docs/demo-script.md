# Three-minute actual-data demonstration

The capture helper requires a completed actual-data run and refuses to record synthetic results. The recorded file is [demo-actual.mp4](demo-actual.mp4), 1280×900 H.264, exactly 180 seconds. It is captioned; no narration or victim imagery is included.

| Time | Demonstration |
| --- | --- |
| 0:00–0:34 | Area/date controls, public original-Sentinel processing and a water model trained on both real training datasets |
| 0:34–1:04 | Actual radar before/after, optical support and 13.6% radar / 31.1% paired clear optical coverage |
| 1:04–1:23 | Modeled road access, baseline connectivity and assumed closures |
| 1:23–2:07 | English/Nepali map-derived answers; unsupported casualty questions return no estimate |
| 2:07–2:22 | Actual Nepali situation report with source dates, uncertainty and attribution |
| 2:22–2:38 | D8 downhill tracing; no hydraulic depth, speed or arrival-time simulation |
| 2:38–3:00 | Source/evaluation separation, official landslide class mismatch, weak held-out recall and remaining independent validation |

Run `python scripts/record_demo.py` using a Python environment with Playwright/Chromium and ffmpeg after starting the application. The earlier synthetic video remains a labeled test artifact, not real-event evidence. Nepali language review and independent field validation remain outstanding.
