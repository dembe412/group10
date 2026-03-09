Distributed Matrix Multiplication (gRPC) with Fault Tolerance

A production-grade distributed matrix multiplication system with **automatic crash recovery** and **worker failure resilience**. The system ensures zero downtime even when workers or the coordinator fails.

## Key Features

✓ **Worker Failure Resilience** - Automatic detection and reassignment of failed chunks  
✓ **Coordinator Crash Recovery** - Resume from checkpoint with saved state  
✓ **Zero Data Loss** - Persistent state saves all progress  
✓ **Adaptive Timeouts** - Adjusts based on worker performance  
✓ **Health Monitoring** - Tracks worker availability and latency  

See [FAULT_TOLERANCE.md](FAULT_TOLERANCE.md) for comprehensive documentation.

## Quick Start

### 1. Installation

```bash
pip install -r requirements.txt
python generate_grpc.py
```

### 2. Start Workers (each in its own terminal)

```bash
python worker.py --port 50051
python worker.py --port 50052
python worker.py --port 50053
```

### 3. Run Coordinator

```bash
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053"
```

Choose input method when prompted:
- Option 1: Random matrices
- Option 2: Manual input

### 4. If Coordinator Crashes (or use Ctrl+C to test)

Resume automatically:
```bash
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053" --resume
```

## Fault Tolerance in Action

### Worker Failure
```
✗ Worker localhost:50051 failed - connection refused
✓ Chunk automatically reassigned to localhost:50052 ✓
```

### Coordinator Crash
```
!!! Computation interrupted
!!! State saved for recovery. Restart with --resume to continue.
```

Restart with recovery:
```bash
CRASH RECOVERY MODE
✓ Found incomplete computation
  Completed chunks: 5
  Partial results: 5

Resuming computation from checkpoint...
```

## Architecture

The system consists of:

1. **coordinator.py** - Orchestrates computation with state persistence
2. **worker.py** - Compute nodes 
3. **state_manager.py** - Persists state for recovery (NEW)
4. **worker_health_monitor.py** - Tracks worker health (NEW)
5. **computation_state/** - Persistent state directory (NEW)

For technical details, see:
- [FAULT_TOLERANCE.md](FAULT_TOLERANCE.md) - Comprehensive architecture and features
- [TESTING_SCENARIOS.md](TESTING_SCENARIOS.md) - Hands-on testing guide

## How It Works

The coordinator:
1. **Probes** workers to check availability
2. **Distributes** matrix rows as chunks to available workers
3. **Persists** progress after each chunk completes
4. **Detects** failures and automatically reassigns work
5. **Recovers** from crashes by resuming from checkpoint

If the coordinator crashes:
1. All state is saved to disk (`./computation_state/`)
2. Restart with `--resume` flag
3. System automatically resumes from last good state
4. Already-computed chunks are skipped (no recomputation)

If a worker crashes:
1. RPC timeout triggers (2-10 seconds)
2. Chunk is reassigned to another healthy worker
3. Retries with exponential backoff
4. No user intervention needed

## Advanced Options

```bash
# Resume from crash
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053" --resume

# Fresh start (disable recovery)
rm -rf ./computation_state/
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053"

# Custom worker list
python coordinator.py --workers "192.168.1.10:50051,192.168.1.11:50051,192.168.1.12:50051"
```

## Testing

See [TESTING_SCENARIOS.md](TESTING_SCENARIOS.md) for:
- Worker failure scenarios
- Coordinator crash recovery
- Multiple simultaneous failures
- Performance under degraded conditions
- Complete testing checklist

## Performance & Reliability

- **Failure Detection**: 2-10 seconds (adaptive based on worker health)
- **Recovery Speed**: Instant (resumes from saved state)
- **Data Loss**: Zero (all progress persisted)
- **Worker Tolerance**: Up to N-1 failures (with N workers)
- **Scalability**: Add workers dynamically

## Example Scenario

```bash
# Terminal 1-3: Start workers
python worker.py --port 50051
python worker.py --port 50052
python worker.py --port 50053

# Terminal 4: Start coordinator
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053"
# Enter: 1 (random matrices), then 10x10

# During computation: Terminal 1 Ctrl+C (stop a worker)
# → Coordinator automatically reassigns that work
# → Computation continues without user intervention

# Mid-way: Terminal 4 Ctrl+C (stop coordinator)
# → State saved to disk

# Terminal 4: Restart with recovery
python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053" --resume
# → System resumes from checkpoint
# → Already-computed chunks skipped
# → Computation completes successfully
```

## State Persistence

During computation, the system saves:
- `active_computation.json` - Current progress and status
- `checkpoint.pkl` - Serialized input matrices
- `partial_results.pkl` - Computed chunk results

These files enable rapid recovery from crashes. They're automatically cleaned up after successful completion.

## Documentation

- [FAULT_TOLERANCE.md](FAULT_TOLERANCE.md) - Full fault tolerance architecture and features
- [TESTING_SCENARIOS.md](TESTING_SCENARIOS.md) - Hands-on testing and verification guide
