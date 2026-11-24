"""HerdBench: grade public crowding and positioning signals against observed, all-account positions.

Quick start::

    import herdbench
    panel = herdbench.load_truth_panel(cohort="non_mm")   # published truth (no addresses, rounded)
    report = herdbench.grade(my_signal)                   # your signal against ni(non_mm), with baselines
    print(report)
"""

__version__ = "1.1.2"

from herdbench.api import GradeReport, grade, load_proxies, load_truth_panel

__all__ = ["GradeReport", "__version__", "grade", "load_proxies", "load_truth_panel"]
