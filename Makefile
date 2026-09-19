.PHONY: check test lint check-live validate-plugin

check: test lint

test:
	python3 -m unittest discover -s tests -t . -v

lint:
	python3 tools/lint.py

# Never a prerequisite of `check`: CI has no `claude` binary on PATH, and
# this must still pass hermetically there. Skips cleanly, rather than
# failing, when `claude` is absent — run it locally after touching either
# manifest.
validate-plugin:
	@if command -v claude >/dev/null 2>&1; then \
		claude plugin validate . && claude plugin validate plugins/agy; \
	else \
		echo "validate-plugin: 'claude' not on PATH, skipping"; \
	fi

# looper: opt-in re-verification against a real, authenticated agy on PATH.
# Spends live agy quota and needs network — never a prerequisite of `check`,
# never invoked by any validator. See tools/live_review_capture.py and
# tools/live_delegate_capture.py. Run these after every agy upgrade: the
# offline suite drives a fake, and a fake is only as good as the last time
# someone checked it against the binary.
check-live:
	python3 tools/live_review_capture.py
	python3 tools/live_delegate_capture.py
	python3 tools/live_lifecycle_capture.py

# The subset of check-live that spends NO agy quota: the agent bind probe and
# the resume silent-fallback trace both resolve before any model call, so an
# intentionally invalid --model captures them for free (Finding A). Cheap
# enough to run on any agy upgrade without thinking about cost.
check-live-free:
	python3 tools/live_delegate_capture.py --free-only
