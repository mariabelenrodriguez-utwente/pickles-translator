SHELL := /bin/bash
IMAGE_NAME := pickles-translator
VERSION_FILE := VERSION

DOCKER_RUN := docker run --rm \
	-v "$(PWD)/input_files:/app/input_files" \
	-v "$(PWD)/output:/app/output" \
	$(IMAGE_NAME):latest

SONAR_COMPOSE := docker compose -f sonar/docker-compose.yml

# Newest STS JSON in output/ (used when STS is not set).
LATEST_STS = $$(ls -t output/*.json 2>/dev/null | head -1)

.PHONY: build build-patch build-minor build-major execute-sts translate-tests visualize test sonar

build:
	@$(MAKE) --no-print-directory _build

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
	[ -z "$(BUMP)" ] || echo "$$new" > $(VERSION_FILE); \
	docker build -t $(IMAGE_NAME):$$new -t $(IMAGE_NAME):latest .; \
	echo "Built $(IMAGE_NAME):$$new"

# Generate STS from all specs in input_files/ (pass SPEC=path to target one file)
execute-sts:
	$(DOCKER_RUN) sts $(if $(SPEC),--spec $(SPEC),)

# Translate test cases to Pickles or Cucumber text.
# Usage: make translate-tests TRACE=test_examples/coffee_tests.txt [STS=...] [TEMPLATE=...] [KEYWORD_MAP=...]
#        make translate-tests JSON=test_examples/detectors_tests.json [STS=...]
# Set one of JSON or TRACE. STS defaults to the newest JSON in output/.
translate-tests:
	@[ -n "$(JSON)$(TRACE)" ] || { echo "Set JSON=... or TRACE=..."; exit 1; }; \
	_sts="$(STS)"; [ -n "$$_sts" ] || _sts=$(LATEST_STS); \
	[ -n "$$_sts" ] || { echo "No STS json found in output/"; exit 1; }; \
	$(DOCKER_RUN) tests --sts "$$_sts" \
		$(if $(JSON),--json $(JSON),) \
		$(if $(TRACE),--trace $(TRACE),) \
		$(if $(TEMPLATE),--cucumber-template $(TEMPLATE),) \
		$(if $(KEYWORD_MAP),--keyword-map $(KEYWORD_MAP),)

# Render a composed STS as DOT and/or HTML.
# Usage: make visualize STS=input_files/coffee_machine_composed_sts.json [FORMAT=html] [ORIGINALS=output/<name>.json]
visualize:
	@[ -n "$(STS)" ] || { echo "Set STS=..."; exit 1; }; \
	$(DOCKER_RUN) visualize --sts $(STS) \
		$(if $(FORMAT),--format $(FORMAT),) \
		$(if $(ORIGINALS),--originals $(ORIGINALS),)

# Run the unit tests with coverage.
test:
	python -m pytest --cov=src --cov-report=xml

# Run a SonarQube scan. Needs SONAR_TOKEN. Starts the server if it is not running.
sonar:
	@[ -n "$$SONAR_TOKEN" ] || { echo "Set SONAR_TOKEN"; exit 1; }; \
	$(SONAR_COMPOSE) up -d && $(SONAR_COMPOSE) run --rm scanner
