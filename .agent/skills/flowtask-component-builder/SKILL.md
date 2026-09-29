---
name: flowtask-component-builder
description: Construct a Flowtask YAML/JSON definition for a specific component. Use this skill when you need to write or understand the configuration for a Flowtask component (e.g. AddDataset, QueryToPandas) by combining its JSON schema and example document.
---

# Flowtask Component Builder

This skill provides instructions on how to correctly write a Flowtask task component definition in YAML or JSON.

## Process

When you need to generate, modify, or validate a Flowtask component definition for a given component name (e.g., `AddDataset`, `QueryToPandas`):

1. **Locate and Read the Schema:**
   Read the JSON schema for the component. It is generally located at:
   `docs/components/<ComponentName>.schema.json`
   Pay close attention to required fields, default values, and data types.

2. **Locate and Read the Example Document:**
   Read the example configuration document for context on real-world usage. It is typically located at:
   `docs/components/<ComponentName>.doc.json`

3. **Synthesize the Configuration:**
   Combine the rules from the schema with the practical example to build the accurate configuration. Note that Flowtask steps are defined as a list of dictionaries where the key is the component name.

4. **Construct the Output:**
   Write the YAML or JSON structure for the task. 

### Example Flowtask YAML Structure

```yaml
name: Task Name
description: Task Description here.
steps:
  - ComponentName:
      use_taskstorage: true
      # ... other properties according to the schema
  - AnotherComponent:
      # ... properties
```

## Important Considerations

- Always prefer using the `view_file` tool to inspect the exact `schema.json` and `doc.json` before writing the configuration. Do not hallucinate component properties.
- Ensure all required parameters specified in the `schema.json` are included in the generated YAML/JSON.
