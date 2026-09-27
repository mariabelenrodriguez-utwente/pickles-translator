*** Settings ***
Documentation    End-to-end tests for the full pipeline with the coffee machine
Resource         ../resources/setup_keywords.resource
Resource         ../resources/preconditions.resource
Resource         ../resources/executions.resource
Resource         ../resources/postconditions.resource
Suite Setup      Ensure Docker Images Are Available

*** Test Cases ***
Coffee Machine Spec Runs Through The Full Pipeline
    Given the STS of "coffee_machine_specification.pickles" is generated
    When the tester runs 3 test cases of 10 steps
    And the traces are translated to Pickles
    Then the STS matches the schema "sts_partial.schema.json"
    And the composed STS exists
    And the traces file has traces
    And the Pickles output contains "Test Case 1:"

Coffee Machine Traces Translate To Cucumber
    Given the STS of "coffee_machine_specification.pickles" is generated
    When the traces "coffee_traces.txt" are translated to Cucumber with template "cucumber_template_coffee.txt"
    Then the feature file contains "Scenario: Test Case 1"
    And the feature file contains "the screen shows "state" equal to IDLE"

Coffee Machine Traces Translate To Cucumber With Keyword Map
    Given the STS of "coffee_machine_specification.pickles" is generated
    When the traces "coffee_traces.txt" are translated to Cucumber with keyword map "coffee_machine_keyword_map.json" and template "cucumber_template_coffee.txt"
    Then the feature file contains "the screen shows the "IDLE" state"
    And the feature file does not contain "the screen shows "state" equal to IDLE"
