# PSSP Datasets

Overview of every dataset used by train-pssp. Rows follow the `soundmap-videos/`
folder tree (first level = collection, second level = sub-collection). Source
rosbags live on two disks (`/media/chen/Extreme SSD/PSSPData` and
`/media/chen/Disk_12T/PSSPData`); the extraction registry is `preprocessing/
build_dataset.py`.

- **Rosbags** — number of source bags in the collection.
- **Samples** — extracted 2 Hz ticks, i.e. usable training samples (`tick_ts`
  length in each npz, summed).
- **Data size** — total on-disk size of the source rosbags (`.db3` + metadata).
- **Duration** — total recording time from each bag's `metadata.yaml`.

| Folder | Subfolder | Rosbags | Samples | Data size | Duration |
|---|---|--:|--:|--:|--:|
| ATR_teleoperation | data_RIKEN_1F | 49 | 36,016 | 120.7 GB | 5.14 h |
|  | data_RIKEN_3f | 28 | 21,635 | 131.4 GB | 3.08 h |
| Demonstration_Data | — | 10 | 375 | 2.6 GB | 0.08 h |
| Meeting | GRP_meeting | 45 | 80,189 | 555.4 GB | 11.26 h |
|  | olab_0630 | 13 | 15,996 | 42.4 GB | 2.26 h |
|  | olab_rev_0630 | 13 | 16,538 | 80.8 GB | 2.33 h |
| WordWolfExp | — | 96 | 47,348 | 301.3 GB | 6.84 h |
| chat | — | 3 | 6,486 | 33.3 GB | 0.91 h |
| egoSAS_demo_data | demo_data_0318_becap | 8 | 1,253 | 6.2 GB | 0.20 h |
|  | egoSAS_test_data | 8 | 10,826 | 129.9 GB | 1.57 h |
|  | kitchen | 2 | 1,331 | 14.2 GB | 0.19 h |
|  | riken_3f | 8 | 3,482 | 15.5 GB | 0.51 h |
| expo_reception_2025 | — | 167 | 188,259 | 2.15 TB | 26.66 h |
| indy_teleoperation | — | 23 | 20,376 | 149.0 GB | 2.90 h |
| **Total** |  | **473** | **450,110** | **3.73 TB** | **63.93 h** |
