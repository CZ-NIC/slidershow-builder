MANIFEST := pyproject.toml
TAG := $(shell grep "^version" $(MANIFEST) | pz --search '"(\d+\.\d+\.\d+(?:-(?:rc|alpha|beta)\.?\d+)?)?"')

.PHONY: release
default: release

release:
	@[ "$$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "Not on main branch"; exit 1; }
	@echo "Tagging release $(TAG)"
	git tag $(TAG)
	git push origin $(TAG)
