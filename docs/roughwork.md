## Decision Tree
- leaf nodes show all combinations possible
- only 372 are Valid combinations

## Generator Layer
- generator_orchestrator (GO) picks valid leaf nodes from decision tree
- GO generates primary-like keys
- Distribute them amongst generators
- Generators take relevant primary keys and generate final data


## Connector Layer
- connectors run on the generated data
- store it in raw_record

- parse it:
  - if parse successful --> store in normalized_record
  - if parse unsuccessful --> store in quarantined_record



## Pulling complete value:


![1789504915711](image/roughwork/1789504915711.png)


