# A33 phone experiment evidence protocol

## Standing rule

After every meaningful real-phone boot experiment, inspect the complete produced result archives before diagnosing the failure or designing the next candidate.

A console excerpt or observer summary is not sufficient when a candidate-specific collector or persistent metadata result exists.

## Required evidence set

When available, preserve and provide:

1. the observer archive from the boot attempt;
2. the candidate-specific result/trace collector archive after exact TWRP restoration;
3. the early root-node/previous-boot archive when the rootfs handoff is uncertain;
4. the exact flash report, candidate manifest, patch report and audit report referenced by those archives.

The user will upload these archives whenever they may materially affect the diagnosis. The assistant must inspect them before requesting another flash.

## Interpretation rules

- Separate phone-runtime failures from host tooling or self-test failures.
- Treat a missing candidate-specific trace as evidence that the code which creates that trace was not reached.
- Do not treat an old persistent metadata file as evidence from the latest boot unless its candidate label, content, timestamp or an experiment-specific marker proves that the latest candidate updated it.
- Treat `last_kmsg` userspace markers as unreliable when the collector reports that they were not preserved or were overwritten.
- Do not infer that an SSH daemon started merely because USB networking and ping work.
- Active `connection-refused` proves packets reached the phone and no listener accepted them; it does not prove why the listener is absent.
- Before changing the next candidate, state explicitly:
  - what the archives prove;
  - what they do not prove;
  - the earliest verified failure boundary;
  - the smallest next phone experiment that can cross or expose that boundary.

## Current U0q v3 lesson

The U0q v3 observer proved a real TWRP transition and stable USB networking, with both ports 22 and 2222 actively refused.

The candidate-specific result archive then proved that `/var/log/a33x-u0q-emergency-ssh.log` was never created and that the inherited U0p trace was unchanged. Therefore the U0q emergency block was not reached. The observer alone could not establish this.

The later U0h archive contains a valid persistent U0h root-node result, but it is labelled `candidate=U0h-userdata-root-node` and is therefore historical evidence, not proof that the current U0q boot reached or repeated that hook. Its collector also reports zero current switch-root, OpenRC and sshd log matches and unreliable userspace preservation in `last_kmsg`.

## Workflow priority

Phone evidence takes priority over adding unrelated host self-tests. Host tests should be run when they validate the exact changed runtime path, but unrelated stale tests must not delay a ready, audited phone experiment.
