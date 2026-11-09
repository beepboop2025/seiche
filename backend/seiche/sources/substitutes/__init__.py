"""Free, republishable substitutes for the FRED series Seiche cannot serve.

One module per publisher source (ALT ids from the 2026-11-09 withheld-data
inventory, section 4.1):

* ``ofr_fsi``: ALT-OFRFSI, OFR Financial Stress Index and contributions
* ``ecb_ciss``: ALT-CISS, ECB New CISS for the United States
* ``frb_gz``: ALT-GZ, Board GZ credit spread and excess bond premium
* ``wb_pink``: ALT-PINK, World Bank Pink Sheet monthly prices and indices
* ``ust_hqm``: ALT-HQM, Treasury HQM par yields and Treasury spreads
* ``frb_cp_bill``: ALT-CPBILL, Board AA financial CP minus the 3-month bill
* ``bis_cbta``: ALT-BISCBTA, BIS central bank total assets for Japan
* ``frb_fcig``: ALT-FCIG, Board FCI-G financial conditions index

``registry`` maps each blocked FRED id to its substitutes and class and is
the only way to produce a servable, labelled row. Nothing in this package
reads FRED, and nothing here is wired into an engine or public surface yet.
"""
