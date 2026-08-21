# Reproducible research notebook

[`seiche_direct_ofr_research.ipynb`](seiche_direct_ofr_research.ipynb) is a
clean nbformat 4 notebook for Colab, Binder, JupyterLab, or local Jupyter. It
has no stored outputs, execution counts, credentials, or environment-specific
paths.

The notebook downloads the two Data Commons candidate CSVs from the public
Seiche repository at commit
`93e83bbc592098fc2f6465ffb49c5e872d61c018`, verifies their SHA-256 digests,
then proves the 10-series/11,163-row allowlist before analysis. It never reads a
Seiche runtime cache and includes no FRED-fetched observation. Daily repo and
monthly MMF clocks remain separate.

The last cell optionally reads the public world-markets REST contract and
prints its citation and clock receipt. A service 503 is recorded as unavailable
rather than treated as an empty or calm reading. This draft has not been
submitted to a notebook catalog and has no DOI.
