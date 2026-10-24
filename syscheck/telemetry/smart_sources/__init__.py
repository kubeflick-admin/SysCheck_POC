"""Layered S.M.A.R.T. sources for the storage collector.

Each source turns what it can read into the normalised per-drive dict from
:mod:`.schema`. ``storage.collect_storage_health`` tries them per drive in
order and keeps the first one that works:

    smartctl     -> bundled smartmontools, JSON output (needs Administrator)
    reliability  -> MSFT_StorageReliabilityCounter (Windows storage stack)
    wmi_smart    -> MSStorageDriver_FailurePredict* raw ATA blobs (fallback)

Parsing is kept apart from the Windows calls so it can be tested on any OS.
"""
