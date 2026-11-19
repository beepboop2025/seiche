# Reproduce the US repo and money market fund snapshot

[Open the Seiche notebook in Google Colab](https://colab.research.google.com/github/beepboop2025/seiche/blob/26e0e053570934d2b0fc779428872b953c2f75a7/notebooks/seiche_direct_ofr_research.ipynb) and choose **Runtime → Run all**.
The notebook checks the files, then shows each series’ unit, row count and first
and last observation dates. The six daily repo series end on August 18 or 19,
2026. The four monthly money market fund series end in June or July 2026.

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
