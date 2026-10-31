"""`python -m provenant.assistant` is the same as `python -m provenant.assistant.cli`."""
from .cli import main

raise SystemExit(main())
