*** Settings ***
Documentation    Suite setup and low-level helpers for the pipeline keywords.
Library          Process
Library          OperatingSystem
Library          Collections
Library          String

*** Variables ***
${REPO_ROOT}          ${CURDIR}${/}..${/}..
${OUTPUT_DIR}         ${REPO_ROOT}${/}output
${RESOURCES_DIR}      ${CURDIR}
${SCHEMA_DIR}         ${REPO_ROOT}${/}schemas
${PYTHON}             python
${TRANSLATOR_IMAGE}    pickles-translator:latest
${TESTER_IMAGE}       lattest-tester:0.2.0

*** Keywords ***
Ensure Docker Images Are Available
    [Documentation]    Builds the translator image fresh. Checks that the tester image exists.
    ${build} =    Run Process    docker    build    -t    ${TRANSLATOR_IMAGE}    ${REPO_ROOT}
    Log    ${build.stdout}\n${build.stderr}
    Should Be Equal As Integers    ${build.rc}    0
    ...    msg=docker build failed for ${TRANSLATOR_IMAGE}:\n${build.stdout}\n${build.stderr}

    ${inspect} =    Run Process    docker    image    inspect    ${TESTER_IMAGE}
    Should Be Equal As Integers    ${inspect.rc}    0
    ...    msg=Tester image '${TESTER_IMAGE}' not found. Build it from system-tests/lattest-tester; this suite does not build it.

Run Translator
    [Documentation]    Runs the translator image with output/ and system-tests/resources/ mounted.
    ...    Fails if the exit code is not 0.
    [Arguments]    @{args}
    ${result} =    Run Process    docker    run    --rm
    ...    -v    ${OUTPUT_DIR}:/app/output
    ...    -v    ${RESOURCES_DIR}:/app/system-tests/resources
    ...    ${TRANSLATOR_IMAGE}    @{args}
    ...    cwd=${REPO_ROOT}
    Log    ${result.stdout}\n${result.stderr}
    Should Be Equal As Integers    ${result.rc}    0
    ...    msg=Translator '${args}[0]' failed:\n${result.stdout}\n${result.stderr}

Get File Basename
    [Documentation]    Strips directory and extension, e.g.
    ...    '.../coffee_machine_specification.pickles' -> 'coffee_machine_specification'.
    [Arguments]    ${path}
    ${name} =    Fetch From Right    ${path}    ${/}
    ${base} =    Fetch From Left    ${name}    .
    RETURN    ${base}

Find Latest File
    [Documentation]    Returns the absolute path of the newest file in ${directory}
    ...    that matches ${pattern}. File names start with a timestamp,
    ...    so alphabetical order is chronological order.
    [Arguments]    ${directory}    ${pattern}
    @{files} =    List Files In Directory    ${directory}    pattern=${pattern}    absolute=True
    Should Not Be Empty    ${files}
    ...    msg=No files matching '${pattern}' found in ${directory}
    @{sorted} =    Sort List    ${files}
    ${latest} =    Get From List    ${sorted}    -1
    RETURN    ${latest}
