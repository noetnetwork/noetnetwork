# Third-party notices

Full licence texts are in `/opt/noet/licenses/` in the image (`licenses/` in the
build context).

- **Qwen3-0.6B** (Copyright Alibaba Cloud, Apache License 2.0). The build
  downloads the weights from Hugging Face at revision
  `c1899de289a04d12100db370d81485cdf75e47ca` and converts them to bf16 GGUF
  (`/models/qwen3-0.6b-bf16.gguf`). The source tree does not contain the
  weights, but a built image does: distributing the image redistributes these
  converted weights under the Apache License 2.0 (`licenses/Apache-2.0.txt`).
- **llama.cpp** (MIT, Copyright (c) 2023-2026 The ggml authors), with NOET's
  proof-of-work patch (MIT). It vendors **cpp-httplib** (MIT, Copyright (c)
  2017 yhirose) and **nlohmann/json** (MIT, Copyright (c) 2013-2025 Niels
  Lohmann).
- **chiavdf** (Copyright 2025 Chia Network Inc., Apache License 2.0), compiled
  for the VDF proof binding, including picosha2 (MIT, Copyright (c) 2017
  okdshin), uint128_t (MIT, Copyright (c) 2013-2017 Jason Lee) and
  xgcd_partial (MIT, Copyright (c) 2012 William Hart).
- **GNU MP** (LGPLv3 or GPLv2), dynamically linked from the Ubuntu package;
  source at https://packages.ubuntu.com. Ubuntu's own copyright files remain
  under `/usr/share/doc/`.
- **NVIDIA CUDA** base images are subject to the NVIDIA Deep Learning Container License.
