import argparse
import grpc
import numpy as np
import matrix_pb2
import matrix_pb2_grpc
import time


def get_matrix_from_user():
    print("Choose input method:")
    print("1) Random matrices")
    print("2) Manual input")
    choice = input("Enter 1 or 2: ").strip()

    if choice == "1":
        r = int(input("Rows for A: ") or 6)
        c = int(input("Cols for A / Rows for B: ") or 6)
        c2 = int(input("Cols for B: ") or c)
        A = np.random.randint(1, 10, (r, c))
        B = np.random.randint(1, 10, (c, c2))
        return A, B

    # manual
    rows = int(input("Rows for A: ").strip())
    cols = int(input("Cols for A / Rows for B: ").strip())
    colsb = int(input("Cols for B: ").strip())

    print(f"Enter {rows} rows for A, each row as {cols} numbers separated by spaces")
    A_rows = []
    for i in range(rows):
        while True:
            line = input(f"A row {i}: ").strip()
            parts = line.split()
            if len(parts) != cols:
                print(f"Expected {cols} numbers")
                continue
            A_rows.append([float(x) for x in parts])
            break

    print(f"Enter {cols} rows for B, each row as {colsb} numbers separated by spaces")
    B_rows = []
    for i in range(cols):
        while True:
            line = input(f"B row {i}: ").strip()
            parts = line.split()
            if len(parts) != colsb:
                print(f"Expected {colsb} numbers")
                continue
            B_rows.append([float(x) for x in parts])
            break

    A = np.array(A_rows)
    B = np.array(B_rows)
    return A, B


def compute_distributed(A, B, workers, retries=2, rpc_timeout=5):
    rows = A.shape[0]
    if rows == 0:
        return np.empty((0, B.shape[1]))

    # create chunks: split into roughly len(workers) chunks
    nchunks = min(rows, max(1, len(workers)))
    chunk_size = rows // nchunks
    chunks = []
    start = 0
    while start < rows:
        end = start + chunk_size
        # ensure last chunk reaches the end
        if end >= rows:
            end = rows
        chunks.append((start, end))
        start = end

    # if chunk_size was 0 (fewer rows than workers), fix by making single-row chunks
    if any(s == e for s, e in chunks):
        chunks = [(i, i + 1) for i in range(rows)]

    results = {}
    alive = {w: True for w in workers}
    handled_by = {}

    for idx, (start, end) in enumerate(chunks):
        success = False
        last_err = None
        preferred = workers[idx % len(workers)]

        for attempt in range(retries + 1):
            # try preferred first, then others
            worker_order = [preferred] + [w for w in workers if w != preferred]
            for w in worker_order:
                if not alive.get(w, False):
                    continue

                try:
                    channel = grpc.insecure_channel(w)
                    # quick check if channel can be ready (short)
                    grpc.channel_ready_future(channel).result(timeout=2)

                    stub = matrix_pb2_grpc.MatrixServiceStub(channel)
                    request = matrix_pb2.MatrixRequest(
                        start_row=start,
                        end_row=end,
                        matrixA=A.flatten().tolist(),
                        matrixB=B.flatten().tolist(),
                        rowsA=A.shape[0],
                        colsA=A.shape[1],
                        colsB=B.shape[1],
                    )

                    response = stub.ComputeRows(request, timeout=rpc_timeout)

                    part = np.array(response.result).reshape(response.rows, response.cols)
                    results[start] = part
                    handled_by[start] = (w, start, end)
                    print(f"Computed rows {start}:{end} from {w}")
                    success = True
                    break

                except Exception as e:
                    last_err = e
                    print(f"Worker {w} failed for rows {start}:{end}: {e}")
                    alive[w] = False
                    continue

            if success:
                break
            # backoff before retry
            time.sleep(0.5 + attempt * 0.5)

        if not success:
            raise RuntimeError(f"Failed to compute rows {start}:{end} after retries: {last_err}")

    # assemble results by start index order
    ordered = [results[s] for s in sorted(results.keys())]
    # summary of which worker handled which chunk
    print("\nAssignment summary:")
    for s in sorted(handled_by.keys()):
        w, sr, er = handled_by[s]
        print(f"  rows {sr}:{er} -> {w}")
    return np.vstack(ordered)


def parse_workers_arg(arg):
    if not arg:
        return ["localhost:50051", "localhost:50052", "localhost:50053"]
    return [x.strip() for x in arg.split(",") if x.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Distributed matrix coordinator")
    parser.add_argument("--workers", type=str, default=None, help="Comma-separated worker addresses (host:port)")
    args = parser.parse_args()

    workers = parse_workers_arg(args.workers)

    A, B = get_matrix_from_user()

    print("Matrix A:\n", A)
    print("Matrix B:\n", B)

    C = compute_distributed(A, B, workers)

    print("\nDistributed Result:\n", C)
