*** Settings ***
Documentation    Given steps: the inputs of the pipeline.
Resource         setup_keywords.resource

*** Keywords ***
The STS of "${spec_filename}" is generated
    [Documentation]    Runs 'sts' on a spec in system-tests/resources/. Sets ${STS_PATH} to the new STS JSON.
    Run Translator    sts    --spec    /app/system-tests/resources/${spec_filename}
    ${basename} =    Get File Basename    ${spec_filename}
    ${sts} =    Find Latest File    ${OUTPUT_DIR}    *_${basename}.json
    Set Test Variable    ${STS_PATH}    ${sts}
