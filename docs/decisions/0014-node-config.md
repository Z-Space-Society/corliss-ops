# ADR-0014: Inference nodes are version, config and data, set with `node-config`

> Status: **Accepted** (2026-11-27). Extends ADR-0010 (scn-config) and
> ADR-0012 (versions are runtime data) to machines CT 100 reaches over SSH.

## Context

Every inference node so far (salmon, orca, GX10, picard) was built by hand.
corliss-ops had the pieces to do it (an `inference_nodes` group,
`enroll-inference-node.yml`, the `nvidia_cuda` and `llama_server` roles) but no
front end, nothing for Intel Max cards, and no record of what a node was
actually running. When a node broke, the question "what did it have, and what
changed?" had no answer short of logging in and looking.

The rest of corliss-ops already works one way: every decision is saved as data
through a front end with a playbook underneath, backups keep that data, and a
cluster is rebuilt from the repo plus what was backed up. Inference nodes are
the part that did not.

The Q02 QuadBox is the first place to change that. It runs Proxmox with two
VMs, one holding the Max 1100s and one holding NVIDIA cards, and gets its own
control node from the usual bootstrap.

## Decision

1. **A system is version, config and data.** Given all three, it can be
   recreated, and nothing else may matter:
   - **Version**: the corliss-ops tag CT 100 is on, recorded as `ops_version`
     on every manifest and facts record.
   - **Config**: every decision someone made, saved as data in the runtime
     inventory (`inventory/local.yml`) or `/root/.zai-secrets`. That covers
     which machine, its name, its address, its GPU vendor and count, and which
     versions it runs.
   - **Data**: what the system produced that can't be derived. `zai-backup`
     keeps it with the config.

   A decision is set through a front end, never by a hand edit on CT 100 or a
   one-off on the box. Anything a system needs that can't be expressed this
   way is a **gap**: it is named in the role's doc, and closing it means
   making it config. It is never written up as a runbook step.

2. **`node-config` is the front end for inference nodes.** It is a whiptail
   menu with `nonint` subcommands in `bin/`, in the same shape as scn-config.
   It is separate from scn-config because it manages a different kind of
   thing: machines reached over SSH that corliss-ops did not create, as
   opposed to containers it creates over the Proxmox API. Folding it in would
   mix two inventories and two trust paths in one menu. The rule from
   ADR-0010 holds for both: a new value gets a menu entry, a `nonint` form and
   a playbook underneath.

3. **A node's inventory entry is its config.** `node-config` writes it through
   `enroll-inference-node.yml`, and nothing else does. `gpu_vendor` places the
   node in `nvidia_nodes` or `intel_xpu_nodes` under `inference_nodes`, and
   that group decides which driver stack it gets. A playbook for one vendor
   targets that vendor's group, so it can never reach the other.

4. **Facts are the evidence.** `node-facts.yml` records, per node and per
   run, the node's config, `ops_version`, OS, kernel, held packages, the GPUs
   on the PCI bus and the driver's own view, in
   `/var/lib/corliss-ops/nodes/<node>/` on CT 100. Timestamped files are kept
   and `zai-backup` takes the directory, so "this node had this config and
   these versions on this date" is a file, and drift is a diff. The health
   check compares what was observed against the config.

5. **A node is a machine at an address.** Whether it is bare metal or a VM
   with passed-through cards makes no difference to corliss-ops. It does not
   create the VMs, configure passthrough or touch the hypervisor's IOMMU
   setup. Those are the machine's build, below the line this repo manages.

6. **One manual step per node, printed whole.** Before Ansible can reach a
   node it needs an `ansible` user with NOPASSWD sudo and CT 100's key.
   `node-config nonint print-node-prep` prints that as one idempotent block
   with the key already filled in, to paste on the node's console. It is the
   node's counterpart to installing git on a Proxmox host (ADR-0008).

7. **New names are corliss-ops names.** Anything added here is named for
   corliss-ops (`node-config`, `ops_version`, `/var/lib/corliss-ops`). The
   checkouts follow the repo's rename in #101: `/opt/corliss-ops` in CT 100 and
   `/root/corliss-ops` on the host. Other `zai` names (`zai-backup`, the
   `zai_ops` key Corliss reads in manifests) stay until renaming one is cheap
   and contained.

## Consequences

- Production nodes stay hand-built until the roles are proven on Q02. They are
  not registered, and nothing here touches them.
- Known gaps, each to be closed by making it config: llama.cpp builds from
  HEAD instead of a recorded ref, the GGUF is staged by hand, Secure Boot is a
  firmware setting the NVIDIA role can only check, and Intel Max nodes have no
  driver role yet.
- A removed node keeps its facts history. Removing drops only its config.
- `inference.yml` now targets `nvidia_nodes`. A node registered before the
  vendor groups existed sits directly in `inference_nodes` and gets no driver
  stack until it is registered again with a vendor.
