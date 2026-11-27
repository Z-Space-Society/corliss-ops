# Role: `intel_xpu`

Builds an Intel Data Center GPU Max 1100 node (`8086:0bda`) on Ubuntu 24.04,
to the state Q01 runs stably: a pinned 6.8 kernel, Intel's LTS 2523 driver
stack, and the driver loaded only by `scn-pvc-init` after it clears the
cards' PCIe 10-bit tags.

- **Source:** [`ansible/roles/intel_xpu/`](../../ansible/roles/intel_xpu/)
- **Applied by:** [`inference.yml`](../../ansible/inference.yml) (`hosts: intel_xpu_nodes`, `become: true`)
- **Target:** an Intel Max inference node (registered with `node-config`, vendor `intel`), over SSH as the `ansible` user

## Purpose

The Max 1100 is supported only by Intel's out-of-tree `intel-i915-dkms`, which
builds only on Ubuntu 24.04's 6.8 GA kernels, and only up to 6.8.0-146
(6.8.0-147 changed `pci_resize_resource`'s signature). Loading it the normal
way can wedge the card or reset the host on root ports that can't complete the
card's 10-bit tags. Everything here is what got Q01 from that to cold boots
with no hands (vault: Projects/QuadBox/Q01, its bring-up log, and the Max 1100
node-build runbook). Where the runbook and Q01 differ, the role follows Q01.

Nothing serves on the node yet; a serving role comes once this stack holds.

## Tasks

Three phases. Each is idempotent: a re-run on a built node changes nothing and
only re-verifies.

| Task | Module | Why |
| ---- | ------ | --- |
| Require Ubuntu 24.04 | `assert` | The stack needs 24.04's 6.8 kernels. |
| Install `mokutil` and `pciutils` | `apt` | To read Secure Boot, and `setpci`/`lspci` for `scn-pvc-init`. |
| Fail if Secure Boot is enabled | `assert` | The driver is built on the node, so it's unsigned. A Proxmox VM on OVMF with pre-enrolled keys boots with Secure Boot on. |
| Never offer a release upgrade | `lineinfile` (`Prompt=never`) | 26.04 would replace the 6.8 kernel the whole stack depends on. |
| **1.** Install the pinned kernel and its fallback | `apt` | Image, headers, modules and modules-extra for `xpu_kernel` and `xpu_fallback_kernel`. |
| Point GRUB at the pin, with the required cmdline | `lineinfile` + `update-grub` | `GRUB_DEFAULT` by title, `GRUB_CMDLINE_LINUX_DEFAULT` = `xpu_kernel_cmdline`, a visible 10 s menu so the fallback can be picked at the console. |
| Boot into the pinned kernel | `reboot` | Only when the running kernel isn't the pin or the cmdline lacks a required parameter. |
| Require the pinned kernel to be running | `assert` | Nothing is purged unless the pin is what booted. |
| **2.** Remove the kernel metapackages | `apt` (`absent`, `purge`) | Holds alone don't stop a metapackage pulling in a new kernel ABI. |
| Purge every other kernel | `package_facts` + `apt` | Every `linux-{image,headers,modules,modules-extra,tools}-6.*` that isn't the pin or the fallback (the installer's kernel, 151 on Q02A). |
| Hold the pinned kernel | `dpkg_selections` | The hold is the pin. |
| Add Intel's key and LTS 2523 repository | `get_url` + `copy` | `noble/lts/2523 unified`, signed by the key in `/usr/share/keyrings/intel-graphics.asc`. |
| Install the Intel driver and compute stack | `apt` (`xpu_packages`) | DKMS builds `intel-i915-dkms` against the pinned kernel here (a few minutes). |
| Hold the Intel stack | `dpkg_selections` (`xpu_held_packages`) | So an upgrade can't move it off what the pinned kernel builds. |
| Require the driver built for the pinned kernel | `command: dkms status` + `assert` | Stops before a reboot into a kernel with no driver. |
| Blacklist i915 | `copy` (`/etc/modprobe.d/zz-no-i915.conf`) + `update-initramfs` | Part of the fix: only `scn-pvc-init` may load i915, after the tags are cleared. |
| Install `scn-pvc-init` and its unit | `copy` | Clears `10BitTagReq` on every `8086:0bda` with `setpci`, verifies, then `modprobe --ignore-install i915`. Refuses to load the driver if a card still has 10-bit tags on. |
| Install `scn-pvc-powercap` and its unit | `copy` + `template` | Sets `xpu_power_limit` W on every card after the driver loads, reads it back, fails if it didn't take. |
| Reboot so the driver loads through `scn-pvc-init` | `reboot` | When the stack, the blacklist or `scn-pvc-init` changed. Never a `modprobe` here: the load has to happen at boot, before anything else touches the cards. |
| Apply a changed power limit | `systemd` (`restarted`) | The limit is runtime only, so a new one restarts `scn-pvc-powercap` instead of rebooting. |
| **3.** Verify | `command` + `assert` | Both units `active`, `10BitTagReq-` on every card, and `xpu-smi discovery` listing `gpu_count` cards. Prints what came up either way. |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/intel_xpu/defaults/main.yml).
Each is a blueprint default from Q01; a node overrides it in its inventory entry.

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `xpu_kernel` | `6.8.0-146-generic` | The pinned, held kernel. Q01 runs it. Nothing from 6.8.0-147 on builds the driver. |
| `xpu_fallback_kernel` | `6.8.0-139-generic` | Kept installed (not held) as a GRUB fallback; the one kernel proven on both Q01 and salmon. |
| `xpu_kernel_cmdline` | `pci=realloc=off` | Required on every node: without it the kernel moves the card's BARs at boot and the card can't complete GPU work. Q01's extra `erst_disable` was only for chasing a reset. |
| `xpu_power_limit` | `150` | Watts per card. **Per-node config:** `node-config nonint set-power-limit <node> <watts>`. 150 is the lowest `xpu-smi` accepts and what Q01 holds with four cards. Raise it only for cooling measured to sustain more. |
| `xpu_repo_key_url`, `xpu_repo` | Intel's GPU repo, `noble/lts/2523 unified` | Where the stack comes from. |
| `xpu_packages` | the runbook's list | Installed. |
| `xpu_held_packages` | driver, firmware, `xpu-smi`, Level Zero, OpenCL, the two dev packages | Held after install. Their versions are `gpu_stack_pins` (group vars, not role defaults, so `node-health.yml` sees them too). |
| `xpu_reboot_timeout` | `900` | Seconds per reboot. |

## Dependencies

- Registered with `node-config` as vendor `intel`, after Prepare (the
  `ansible` user, its sudo and CT 100's key).
- Secure Boot off in firmware.
- **On bare metal:** the board settings in its vault note (Above 4G decoding
  and Re-Size BAR on, SR-IOV off on the Tyan), cards in CPU-attached x16 slots,
  cooling fitted before power-on.
- **In a VM:** the cards passed through whole, and the VM's 64-bit MMIO window
  large enough for their BARs. Both are the hypervisor's job, outside
  corliss-ops (ADR-0014).

## Verify

```bash
# from CT 100
ansible-playbook inference.yml --limit q02a      # ends with "i915 loaded by scn-pvc-init, ..."
node-config nonint health q02a                   # driver check passes with xpu-smi seeing gpu_count
```

On the node, the runbook's checks:

```bash
cat /proc/cmdline                                    # contains pci=realloc=off
journalctl -b -u scn-pvc-init -u scn-pvc-powercap    # "10-bit tags cleared", "power limit N W" per card
sudo lspci -d 8086:0bda -vvv | grep DevCtl2:         # 10BitTagReq- on every card
sudo xpu-smi discovery                               # every card listed
```

## Notes and known gaps

These are the gaps in the ADR-0014 sense: each one is a decision or a step
that isn't config yet.

- **Package versions are pinned to Q01's.** `gpu_stack_pins` in
  [`group_vars/intel_xpu_nodes.yml`](../../ansible/group_vars/intel_xpu_nodes.yml)
  holds the exact version of each held package, read from Q01 on 2026-11-27;
  the role installs those and the health check fails a node that drifts. If
  Intel's repo stops carrying one, the install fails rather than taking a
  newer build. Moving on is a change to that file, tried on one node first.
- **Untested in a VM.** Q02A is the first. In particular, whether the guest's
  `setpci` write to the card's DevCtl2 reaches the device through the
  passthrough is unproven. If it doesn't, `scn-pvc-init` refuses to load the
  driver (it fails safe), and the fix would move to the Proxmox host.
- **Fans and thermal limits are outside the node** on a QuadBox: Q01's
  `fan` script drives the board's IPMI. Run the fans at full before any load.
  An automatic, workload-independent thermal guard is still open (Max 1100
  goal 2 in the vault).
- **Not here from the runbook:** the model-weights LV (a disk layout
  decision per machine), the known-good LVM snapshot (a VM has Proxmox
  snapshots), `scn-loadtest` and `scn-soak`.
- **Recovery:** the GRUB menu stays visible for 10 s. At the console, pick
  the fallback kernel, or add `module_blacklist=i915` to boot without the
  driver.
