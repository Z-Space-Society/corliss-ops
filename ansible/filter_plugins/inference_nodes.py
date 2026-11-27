# Edit an inference node's entry in the runtime inventory (docs/decisions/0014).
#
#   data | inference_node_entry(name)                 -> the node's entry, {} if none
#   data | inference_node_set(name, group, entry)     -> data with the node in group
#   data | inference_node_drop(name)                  -> data without the node
#
# `data` is inventory/local.yml as a mapping. A node lives in exactly one
# vendor group under all.children.inference_nodes.children. An entry written
# before the vendor groups existed sits in inference_nodes.hosts itself, so
# every lookup and removal checks there too, and setting a node moves it out of
# wherever it was. Nothing else in local.yml is touched.

import copy

from ansible.errors import AnsibleFilterError

VENDOR_GROUPS = ("nvidia_nodes", "intel_xpu_nodes")


def _host_maps(group):
    """Every hosts mapping a node could sit in, creating none."""
    maps = []
    if isinstance(group.get("hosts"), dict):
        maps.append(group["hosts"])
    children = group.get("children") or {}
    for name in VENDOR_GROUPS:
        hosts = (children.get(name) or {}).get("hosts")
        if isinstance(hosts, dict):
            maps.append(hosts)
    return maps


def _inference_group(data, create):
    if not isinstance(data, dict):
        raise AnsibleFilterError("the runtime inventory is not a mapping")
    if not create:
        return (((data.get("all") or {}).get("children") or {})
                .get("inference_nodes") or {})
    all_ = data.setdefault("all", {}) or {}
    data["all"] = all_
    children = all_.setdefault("children", {}) or {}
    all_["children"] = children
    group = children.setdefault("inference_nodes", {}) or {}
    children["inference_nodes"] = group
    return group


def inference_node_entry(data, name):
    for hosts in _host_maps(_inference_group(data or {}, False)):
        if name in hosts:
            return dict(hosts[name] or {})
    return {}


def inference_node_drop(data, name):
    data = copy.deepcopy(data or {})
    for hosts in _host_maps(_inference_group(data, False)):
        hosts.pop(name, None)
    return data


def inference_node_set(data, name, group_name, entry):
    if group_name not in VENDOR_GROUPS:
        raise AnsibleFilterError(
            "inference_node_set: %r is not one of %s" % (group_name, ", ".join(VENDOR_GROUPS)))
    data = inference_node_drop(data, name)
    group = _inference_group(data, True)
    children = group.setdefault("children", {}) or {}
    group["children"] = children
    vendor = children.setdefault(group_name, {}) or {}
    children[group_name] = vendor
    hosts = vendor.setdefault("hosts", {}) or {}
    vendor["hosts"] = hosts
    hosts[name] = dict(entry)
    return data


class FilterModule:
    def filters(self):
        return {
            "inference_node_entry": inference_node_entry,
            "inference_node_set": inference_node_set,
            "inference_node_drop": inference_node_drop,
        }
