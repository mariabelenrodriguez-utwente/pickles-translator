SHELL := /bin/bash
IMAGE_NAME := pickles-transducer
VERSION_FILE := VERSION

.PHONY: build-patch build-minor build-major execute-sts translate-tests sts-dot-to-png

build-patch:
	@$(MAKE) --no-print-directory _build BUMP=patch

build-minor:
	@$(MAKE) --no-print-directory _build BUMP=minor

build-major:
	@$(MAKE) --no-print-directory _build BUMP=major

_build:
	@v=$$(cat $(VERSION_FILE) 2>/dev/null || echo "0.1.0"); \
	IFS='.' read -r major minor patch <<< "$$v"; \
	case "$(BUMP)" in \
		patch) patch=$$((patch+1)) ;; \
		minor) minor=$$((minor+1)); patch=0 ;; \
		major) major=$$((major+1)); minor=0; patch=0 ;; \
	esac; \
	new="$$major.$$minor.$$patch"; \
	echo "$$new" > $(VERSION_FILE); \
	docker build -t $(IMAGE_NAME):$$new -t $(IMAGE_NAME):latest .; \
	echo "Built $(IMAGE_NAME):$$new"

# Generate STS from all specs in input_files/ (pass SPEC=path to target one file)
execute-sts:
	docker run --rm \
		-v "$(PWD)/input_files:/app/input_files" \
		-v "$(PWD)/output:/app/output" \
		$(IMAGE_NAME):latest sts $(if $(SPEC),--spec $(SPEC),)

# Translate pre-generated test cases to NL text
# Usage: make translate-tests [STS=output/foo_composed.json] TESTS=output/foo_tests.json
translate-tests:
	@_sts=$${STS:-$$(ls -t output/*_composed.json 2>/dev/null | head -1)}; \
	[ -n "$$_sts" ] || { echo "No *_composed.json found in output/"; exit 1; }; \
	docker run --rm \
		-v "$(PWD)/output:/app/output" \
		$(IMAGE_NAME):latest tests --sts "$$_sts" --tests $(TESTS)


# Convert the most recently generated *_composed.dot to PNG
sts-dot-to-png:
	@dot=$$(ls -t output/*_composed.dot 2>/dev/null | head -1); \
	[ -n "$$dot" ] || { echo "No *_composed.dot found in output/"; exit 1; }; \
	png="$${dot%.dot}.png"; \
	dot -Tpng "$$dot" -o "$$png"; \
	echo "Written $$png"