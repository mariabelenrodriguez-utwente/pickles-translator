*** Settings ***
Documentation    Then steps: checks on the pipeline outputs.
Resource         setup_keywords.resource

*** Keywords ***
The STS matches the schema "${schema_name}"
    [Documentation]    Validates ${STS_PATH} against a schema in schemas/.
    ${result} =    Run Process    ${PYTHON}    ${REPO_ROOT}${/}system-tests${/}scripts${/}validate_schema.py
    ...    ${SCHEMA_DIR}${/}${schema_name}    ${STS_PATH}
    ...    cwd=${REPO_ROOT}
    Log    ${result.stdout}\n${result.stderr}
    Should Be Equal As Integers    ${result.rc}    0
    ...    msg=${STS_PATH} does not conform to ${schema_name}:\n${result.stderr}

The composed STS exists
    File Should Exist    ${COMPOSED_PATH}

The traces file has traces
    [Documentation]    ${TRACES_PATH} starts with a trace ('Just [').
    ${content} =    Get File    ${TRACES_PATH}
    Should Start With    ${content}    Just [

The Pickles output contains "${text}"
    File Should Contain Text    ${PICKLES_PATH}    ${text}

The feature file contains "${text}"
    File Should Contain Text    ${FEATURE_PATH}    ${text}

The feature file does not contain "${text}"
    ${content} =    Get File    ${FEATURE_PATH}
    Should Not Contain    ${content}    ${text}

File Should Contain Text
    [Arguments]    ${path}    ${text}
    ${content} =    Get File    ${path}
    Log    ${content}
    Should Contain    ${content}    ${text}
