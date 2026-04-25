# Distributed P2P Matrix Multiplication System

A **fully decentralized** distributed matrix multiplication system built with Python and gRPC. All nodes are equal peers — no master/slave architecture. State is synchronized automatically via gossip protocol, and chunk routing uses consistent hashing.

## Architecture Highlights

### Peer-to-Peer (P2P) — No Master/Slave
- All nodes are equal peers with no coordinator bottleneck
- Automatic peer discovery via bootstrap seeds
- Decentralized state management using CRDTs

### Consistent Hashing (DHT)
- Deterministic chunk-to-peer mapping (O(log N) lookups)
- 160 virtual nodes per peer for even load distribution
- Automatic failover to replica nodes when primary is unavailable

### Gossip Protocol
- Epidemic state propagation (O(log N) convergence rounds)
- < 500ms convergence for 5 nodes, ~2s for 20 nodes
- Message deduplication and bandwidth-efficient batching

### Strong Eventual Consistency (CRDT)
- Vector clocks track causality across nodes
- Deterministic conflict resolution — all nodes converge to identical state
- No data loss or corruption during concurrent updates

### Automatic Failure Recovery
- Circuit breaker pattern for failed peers
- Exponential backoff (100ms → 30s)
- Automatic recovery detection and replica failover

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
python -m scripts.generate_grpc  # Generate gRPC stubs from matrix.proto
```

### 2. Start Peer Nodes

If you are a regular user, simply **double-click the generated `.exe` file** or run the script without any arguments:

```bash
python -m scripts.run_node
```

This will launch a friendly interactive menu:
```
1. Start a New Network (I am the first PC)
2. Join an Existing Network
```
Just follow the prompts on the screen!

#### Advanced / Developer Start

If you prefer to bypass the menu, you can still use the traditional command-line arguments. Open **three separate terminals** and start each node from the project root:

```bash
# Terminal 1 — Bootstrap node
python -m scripts.run_node --node-id node-0 --port 50051

# Terminal 2
python -m scripts.run_node --node-id node-1 --port 50052 --seeds node-0@localhost:50051

# Terminal 3
python -m scripts.run_node --node-id node-2 --port 50053 --seeds node-0@localhost:50051
```

Or start all nodes at once:

```bash
python -m scripts.start_peer_nodes
```

### 3. Run the Client

In a **fourth terminal**, launch the interactive matrix client:

```bash
python -m core.matrix_client
```

To run a concurrent stress test, you can use:
```bash
python -m tests.stress_test
```

The client provides an interactive menu to:
1. **Input matrices** — choose dimensions and generation method (random, integers, manual input, etc.)
2. **Distribute computation** — chunks are assigned to peer nodes via consistent hashing
3. **Collect and verify results** — results are gathered via gRPC and verified against a local reference computation

### Multi-PC Deployment

To run nodes across different machines on the same network (ensure you are running from the project root directory):

```bash
# On Machine A (192.168.1.10)
python -m scripts.run_node --node-id node-0 --host 0.0.0.0 --port 50051

# On Machine B (192.168.1.11)
python -m scripts.run_node --node-id node-1 --host 0.0.0.0 --port 50051 --seeds node-0@192.168.1.10:50051

# On Machine C — run the client
python -m core.matrix_client --seeds node-0@192.168.1.10:50051,node-1@192.168.1.11:50051
```

## Project Structure

```
group10/
├── core/
│   ├── p2p_node.py            # Core P2P node (gRPC server, gossip, health loops)
│   ├── consistent_hash.py     # Consistent hash ring (DHT) for chunk routing
│   ├── distributed_state.py   # CRDT-based state with vector clocks
│   ├── assignment_strategy.py # Strategy for task assignment
│   └── matrix_client.py       # Interactive client for matrix computation
├── network/
│   ├── peer_discovery.py      # Peer registration and bootstrap
│   ├── peer_health.py         # Health monitoring with circuit breaker
│   └── gossip_manager.py      # Epidemic state propagation
├── grpc_layer/
│   ├── matrix.proto           # gRPC/Protobuf service definitions
│   ├── matrix_pb2.py          # Generated Protobuf code
│   └── matrix_pb2_grpc.py     # Generated gRPC service stubs
├── scripts/
│   ├── run_node.py            # CLI to start a single peer node
│   ├── start_peer_nodes.py    # Convenience script to start a local cluster
│   └── generate_grpc.py       # Regenerate gRPC files from matrix.proto
├── tests/
│   └── stress_test.py         # Concurrent stress testing script
├── requirements.txt           # Python dependencies
└── .gitignore
```

## Core Components

### P2P Node (`p2p_node.py`)
Central class managing all peer operations — consistent hashing, peer discovery, gossip sync, health monitoring, and the gRPC server for receiving chunk computation requests from other peers and the client.

### Consistent Hash Ring (`consistent_hash.py`)
Deterministic chunk-to-peer mapping using 160 virtual nodes per peer. Provides O(log N) lookups and supports replica selection for fault tolerance.

### Distributed State (`distributed_state.py`)
CRDT-based shared state with vector clocks for causality tracking. Supports conflict-free merging of updates from multiple nodes.

### Gossip Manager (`gossip_manager.py`)
Epidemic message propagation with configurable fanout. Includes message deduplication and TTL-based expiry to limit bandwidth usage.

### Peer Discovery (`peer_discovery.py`)
Decentralized peer registration via bootstrap seeds. Peers are discovered automatically and expired after a configurable timeout.

### Peer Health (`peer_health.py`)
Circuit breaker pattern for failure detection. Tracks RPC success/failure rates and transitions peers through HEALTHY → DEGRADED → UNHEALTHY states with exponential backoff.

### Matrix Client (`matrix_client.py`)
Interactive client that accepts matrix dimensions, generates matrices (random, integer, manual input), distributes chunk computations to peer nodes via gRPC, collects results, and verifies correctness against a local reference computation.

## Failure Scenarios & Recovery

| Scenario | Detection | Response | Recovery |
|---|---|---|---|
| **Peer Failure** | 3 consecutive RPC failures | Circuit breaker opens | Exponential backoff + automatic retry |
| **Network Partition** | Peer becomes unreachable | Marked UNHEALTHY | Automatic when partition heals |
| **Cascade Failure** | Multiple nodes fail | Chunks re-routed to replicas | Gradual as nodes come back online |

In all cases, vector clocks ensure strong eventual consistency — no data loss or corruption.

## Performance

| Metric | Result |
|---|---|
| Hash lookups | 1,750+ /sec |
| State updates | 333,915+ /sec |
| Convergence (5 nodes) | < 500ms |
| Convergence (20 nodes) | ~2s |

## Dependencies

- **Python 3.8+**
- `grpcio` ≥ 1.50.0
- `grpcio-tools` ≥ 1.50.0
- `numpy` ≥ 1.20.0

## License

This project was developed as part of a group assignment.
## Propagation & Distributed Operation

The system propagates state and computation across all peers using a combination of:

- **Gossip Protocol**: Periodic epidemic‑style dissemination of state updates. Each node selects a subset of peers to push its local view of the distributed state. Updates are merged using CRDT rules, ensuring eventual consistency.
- **CRDT (Vector Clock) State**: Every update carries a vector clock. Merges are conflict‑free and deterministic, guaranteeing that all nodes converge to the same state without central coordination.
- **Consistent Hash Ring**: Determines which peer is responsible for each matrix chunk. When a node fails, the next highest‑weight replica (as provided by the Rendezvous strategy) automatically takes over the chunk.
- **Circuit Breaker & Exponential Backoff**: Detects unresponsive peers, temporarily disables them, and retries after a back‑off period, triggering re‑routing of pending chunks.

### Data Flow Example

1. **Client** generates matrix chunks and computes a hash key for each chunk.
2. **Consistent Hash** maps each key to a primary peer and optionally N‑1 replica peers.
3. The client sends a gRPC request to the primary peer. The peer begins computation.
4. Upon completion, the peer updates its local CRDT state with the result and **gossips** this update to a random fan‑out of peers.
5. All peers merge the update; vector clocks guarantee that later updates win over earlier ones.
6. If the primary peer becomes unreachable, replicas already hold the result and can serve the client after a short fail‑over interval.

![System Architecture](file:///C:/Users/Izaek/.gemini/antigravity/brain/61ff80ec-0cf8-464d-8f93-900cbca43a79/system_architecture_1777122487169.png)

### Scaling Out

- Adding a new node requires only launching `run_node.py` with a unique `--node-id` and optional `--seeds`. The node will automatically join the hash ring and start gossiping.
- The consistent hash ring rebalances automatically; existing chunk assignments migrate lazily as peers request needed data.
- Network partitions heal automatically: once connectivity is restored, gossip synchronizes divergent states.

### Monitoring & Observability

- Each node logs health metrics (`peer_health.py`) and exposes a simple gRPC `HealthCheck` service.
- The client can query peer health to make informed routing decisions.
- Logs are aggregated in `p2p_system.log` for post‑mortem analysis.
