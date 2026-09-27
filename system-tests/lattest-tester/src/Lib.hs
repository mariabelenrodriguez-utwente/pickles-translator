module Lib
    ( run
    ) where

import Lattest.Model.Automaton (prependOutputChecks)
import Lattest.Model.StandardAutomata
import Lattest.Model.Symbolic.SolveSTS (offlineTests, toTrace)
import Lattest.Exec.StandardTestControllers
import Lattest.Exec.StandardTestControllers.CompleteTestSuite(randomCoveringTestSelectorFromSeed, allSwitches, isInputSwitch, offlineTestsSwitches)
import Lattest.Util.STSJSONParser (stsListFromJSONFile)
import Lattest.Exec.Testing (Verdict(..))
import Lattest.Model.BoundedMonad (BoundedConfiguration(..))
import qualified Data.Map as Map
import qualified Data.Set as Set
import Lattest.Util.STSJSONWriter (stsToJSONFile)
import Data.Tuple (swap)
import Control.Monad (foldM_)
import System.Environment (getArgs)
import System.FilePath (takeBaseName)

-- Arguments: SPEC_JSON COMPOSED_JSON TRACES_TXT N_TESTS N_STEPS
run :: IO ()
run = do
    [specPath, composedPath, tracesPath, testsArg, stepsArg] <- getArgs
    let nrTests = read testsArg :: Int
        nrSteps = read stepsArg :: Int
    result <- stsListFromJSONFile specPath
    stss <- case result of
        Left  err -> error $ "failed to parse STS JSON: " ++ err
        Right r   -> return r
    -- Compose all parsed STSs
    let checked  = [ (sid, prependOutputChecks (\/) ("check_" ++) sts) | (sid, sts, _, _, _) <- stss ]
        conjmodel   = conjunctionAll checked
        seqSelfComposed = conjmodel |>> conjmodel
        seqComposed = conjmodel |> seqSelfComposed
        initVal  = case stss of
            [] -> error "no STSs loaded"
            (_, _, _, _, val):_ -> val    -- TODO: now each STS has its initial valuation, but this should be common as we are representing a single system
        model    = interpretSTS seqComposed initVal
        gs = Map.fromList $ map swap $ Map.toList $ Map.unions $ map (\(_,_,g,_,_) -> g) stss
        as = Map.fromList $ map swap $ Map.toList $ Map.unions $ map (\(_,_,_,a,_) -> a) stss

    stsToJSONFile composedPath (takeBaseName specPath) seqComposed gs as initVal
    writeFile tracesPath ""
    let randomSeed = 456
        observeVerdict (Just _) _ _ _ = error "shouldn't happen?"
        observeVerdict Nothing _ _ lattice
            | isForbidden lattice = pure $ Just Fail
            | isUnderspecified lattice = pure $ Just Pass
            | otherwise = pure Nothing
        switches      = allSwitches model
        inputSwitches = Set.filter isInputSwitch switches
    -- the covered switches are carried from one test case to the next
    let testCase coveredSwitches n = do
            -- target the (location, gate) pairs of input switches not covered yet
            let toCover    = Set.map (\(l, t, _, _) -> (l, t)) (inputSwitches Set.\\ coveredSwitches)
                controller = randomCoveringTestSelectorFromSeed model (Just toCover) (randomSeed + n) `untilCondition` stopAfterSteps nrSteps `observingOnly` observer Nothing observeVerdict pure
            tests <- offlineTests model controller
            appendFile tracesPath (show (toTrace model tests) ++ "\n")

            let coveredSwitches' = coveredSwitches `Set.union` offlineTestsSwitches model tests
            putStrLn $ "after test " ++ show (n + 1) ++ ": switch coverage "
                ++ show (Set.size coveredSwitches') ++ "/" ++ show (Set.size switches)
                ++ ", input switch coverage "
                ++ show (Set.size (Set.filter isInputSwitch coveredSwitches')) ++ "/" ++ show (Set.size inputSwitches)
            pure coveredSwitches'
    foldM_ testCase Set.empty [0 .. nrTests - 1]
