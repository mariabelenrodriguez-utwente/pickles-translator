*** Settings ***
Documentation    When steps: the tester and the translator runs.
Resource         setup_keywords.resource

*** Keywords ***
The tester runs ${nr_tests} test cases of ${nr_steps} steps
    [Documentation]    Runs the tester on ${STS_PATH}. Sets ${COMPOSED_PATH} and ${TRACES_PATH} (in output/).
    ${sts_basename} =    Get File Basename    ${STS_PATH}
    ${sts_filename} =    Fetch From Right    ${STS_PATH}    ${/}
    ${result} =    Run Process    docker    run    --rm
    ...    -v    ${OUTPUT_DIR}:/data
    ...    ${TESTER_IMAGE}
    ...    /data/${sts_filename}    /data/${sts_basename}_composed.json    /data/${sts_basename}_traces.txt
    ...    ${nr_tests}    ${nr_steps}
    ...    cwd=${REPO_ROOT}
    Log    ${result.stdout}\n${result.stderr}
    Should Be Equal As Integers    ${result.rc}    0
    ...    msg=Tester failed:\n${result.stdout}\n${result.stderr}
    Set Test Variable    ${COMPOSED_PATH}    ${OUTPUT_DIR}${/}${sts_basename}_composed.json
    Set Test Variable    ${TRACES_PATH}      ${OUTPUT_DIR}${/}${sts_basename}_traces.txt

The traces are translated to Pickles
    [Documentation]    Runs 'tests --trace' on ${TRACES_PATH}. Sets ${PICKLES_PATH}.
    ${traces_filename} =    Fetch From Right    ${TRACES_PATH}    ${/}
    ${pickles} =    Translate Traces    /app/output/${traces_filename}    pickles
    Set Test Variable    ${PICKLES_PATH}    ${pickles}

The traces "${traces}" are translated to Cucumber with template "${template}"
    [Documentation]    Runs 'tests --trace' on a traces file, with a template
    ...    in system-tests/resources/. Sets ${FEATURE_PATH}.
    ${feature} =    Translate Traces    /app/system-tests/resources/${traces}    feature
    ...    --cucumber-template    /app/system-tests/resources/${template}
    Set Test Variable    ${FEATURE_PATH}    ${feature}

The traces "${traces}" are translated to Cucumber with keyword map "${keyword_map}" and template "${template}" 
    [Documentation]    Runs 'tests --trace' on a traces file, with a template
    ...    and keyword map in system-tests/resources/. Sets ${FEATURE_PATH}.
    ${feature} =    Translate Traces    /app/system-tests/resources/${traces}    feature
    ...    --cucumber-template    /app/system-tests/resources/${template}
    ...    --keyword-map    /app/system-tests/resources/${keyword_map}
    Set Test Variable    ${FEATURE_PATH}    ${feature}

Translate Traces
    [Documentation]    Runs 'tests --trace' on ${STS_PATH} and returns the path of the new output file.
    ...    ${trace_path} is a path in the container. ${extension} is 'pickles' or 'feature'.
    [Arguments]    ${trace_path}    ${extension}    @{extra}
    ${sts_filename} =    Fetch From Right    ${STS_PATH}    ${/}
    ${basename} =    Get File Basename    ${STS_PATH}
    Run Translator    tests    --sts    /app/output/${sts_filename}    --trace    ${trace_path}    @{extra}
    ${out} =    Find Latest File    ${OUTPUT_DIR}    *_${basename}_test_cases.${extension}
    RETURN    ${out}
