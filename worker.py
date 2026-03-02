import argparse
import grpc
from concurrent import futures
import numpy as np
import matrix_pb2
import matrix_pb2_grpc
import time


class MatrixService(matrix_pb2_grpc.MatrixServiceServicer):

    def ComputeRows(self, request, context):
        # Start timing the computation
        start_time = time.time()

        A = np.array(request.matrixA).reshape(
            request.rowsA, request.colsA
        )

        B = np.array(request.matrixB).reshape(
            request.colsA, request.colsB
        )

        start = request.start_row
        end = request.end_row

        peer = None
        try:
            peer = context.peer()
        except Exception:
            peer = "unknown"

        print(f"\n{'='*80}")
        print(f"WORKER COMPUTATION START")
        print(f"{'='*80}")
        print(f"Request from: {peer}")
        row_list = ", ".join(str(i) for i in range(start, end))
        print(f"Computing rows: {row_list}")
        print(f"Matrix A shape: {A.shape}")
        print(f"Matrix B shape: {B.shape}")
        print(f"Output dimensions: {end - start} rows × {B.shape[1]} columns")
        print(f"{'='*80}\n")

        # compute row-by-row and log detailed operations
        partial_rows = []
        row_times = []
        
        for global_row in range(start, end):
            row_start_time = time.time()
            a_row = A[global_row]
            row_result = []
            
            print(f"[ROW {global_row}] Processing...")
            print(f"  Input row from A: {[int(x) for x in a_row.tolist()]}")
            print(f"  Computing {B.shape[1]} column operations:")
            
            for j in range(B.shape[1]):
                b_col = B[:, j]
                # Detailed dot product computation
                value = int(np.dot(a_row, b_col))
                row_result.append(value)
                
                # Show computation breakdown
                multiplications = [f"{int(a_row[k])}×{int(b_col[k])}" for k in range(len(a_row))]
                print(f"    C[{global_row},{j}] = {' + '.join(multiplications)} = {value}")
            
            partial_rows.append(row_result)
            row_time_ms = (time.time() - row_start_time) * 1000
            row_times.append(row_time_ms)
            print(f"  ✓ Row {global_row} completed in {row_time_ms:.2f} ms\n")

        partial = np.array(partial_rows)
        computation_time_ms = (time.time() - start_time) * 1000
        
        print(f"{'='*80}")
        row_list_result = ", ".join(str(i) for i in range(start, end))
        print(f"COMPUTATION RESULTS (Rows: {row_list_result})")
        print(f"{'='*80}")
        print(f"Result matrix shape: {partial.shape}")
        print(f"Result:\n{partial.astype(int)}")
        print(f"\nTiming Summary:")
        for i, row_idx in enumerate(range(start, end)):
            print(f"  Row {row_idx}: {row_times[i]:.2f} ms")
        print(f"Total computation time: {computation_time_ms:.2f} ms")
        print(f"{'='*80}\n")

        return matrix_pb2.MatrixReply(
            result=partial.flatten().tolist(),
            rows=partial.shape[0],
            cols=partial.shape[1],
            computation_time_ms=int(round(computation_time_ms))
        )


def serve(port):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    matrix_pb2_grpc.add_MatrixServiceServicer_to_server(
        MatrixService(), server
    )

    server.add_insecure_port(f"[::]:{port}")
    server.start()

    print(f"Worker running on port {port}")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("Shutting down worker")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Matrix worker server")
    parser.add_argument("--port", type=int, default=50051, help="Port to listen on")
    args = parser.parse_args()

    serve(args.port)
