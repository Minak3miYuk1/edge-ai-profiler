// BCC source: one target process's pre-registered TIDs, never current-PID filtering.
// Counters are cumulative per CPU; user space reads deltas without clearing maps.
#include <uapi/linux/ptrace.h>

BPF_HASH(targets, u32, u8, 1024);
BPF_ARRAY(control, u32, 1);
BPF_PERCPU_ARRAY(counts, u64, 2); // 0 = switch-out, 1 = switch-in

TRACEPOINT_PROBE(sched, sched_switch)
{
    u32 zero = 0;
    u32 *enabled = control.lookup(&zero);
    if (!enabled || !*enabled)
        return 0;
    u32 prev = args->prev_pid;
    u32 next = args->next_pid;
    if (targets.lookup(&prev)) {
        u64 *out = counts.lookup(&zero);
        if (out)
            (*out)++;
    }
    if (targets.lookup(&next)) {
        u32 one = 1;
        u64 *in = counts.lookup(&one);
        if (in)
            (*in)++;
    }
    return 0;
}
