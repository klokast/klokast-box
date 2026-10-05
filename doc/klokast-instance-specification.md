# Instance desired state

`klokast-instance.json` is the desired state of one deployment. Keep it in a
private, standalone Git repository. It contains boxes, controller placement,
Tailnet members, connectivity capabilities, application placement and features,
retained data, and maintenance policy. It must not contain secrets, runtime
observations, generated configuration, or executable code.

The installed CLI selects validation from `schema-version`. The `$schema` URL
is information for editors. It does not select privileged code. There is no
engine lock. The deployment property described in
[Platform lifecycle](platform-lifecycle.md) determines which implementation may
run.

The public [Instance schema](../schemas/klokast-instance-v1.schema.json) defines
the accepted fields. Public application manifests define available features
and placement modes. A deployment can have one box and no standby controller.
Applications can be present. A missing adapter is an error; it does not permit
a generic deployment or removal.

Use `klokast init --instance PATH --values FILE` to create an Instance. Use
`klokast check --instance PATH` before committing a change. `klokast plan`
shows a preview. An optional Observation supplies facts for comparison only.
Neither command writes runtime state or creates execution authority.

The controller checkout is `~/private/klokast/instance`. Use ordinary Git to
edit, commit, push, and synchronize it. Legacy YAML files and observations
cannot replace the Instance as desired state. Retained data declarations remain
binding when an application is absent. Omission does not permit data deletion.
