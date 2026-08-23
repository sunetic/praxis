# Knowledge Bases

Knowledge bases let Agents use team documentation, runbooks, incident-severity rules, and other local material alongside live database evidence. They are useful for answering “what does our policy say?” but do not replace querying current database state.

## Basic workflow

1. Create a knowledge base with a name, description, and tags.
2. Upload Markdown (`.md`) documents.
3. Wait for processing to finish and inspect the content on the details page.
4. Ask an Agent to use both the knowledge-base rules and live database evidence.

Some distributions can install a collection of built-in knowledge content and manage its installation state from the knowledge-base list.

## Reliable use

- State the applicable system, version, and update date in each document.
- Cite policy thresholds separately from live metrics so old documents are not mistaken for current facts.
- For critical conclusions, require the Agent to identify the kind of evidence it used.
- After updating rules, rerun relevant Eval cases to verify retrieval and decisions.

Knowledge-base content becomes part of model context. Do not upload sensitive material that the configured model service should not process.
