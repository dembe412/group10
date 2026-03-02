import argparse
import grpc
from concurrent import futures
import numpy as np
import matrix_pb2
import matrix_pb2_grpc


class MatrixService(matrix_pb2_grpc.MatrixServiceServicer):

    def ComputeRows(self, request, context):

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

        print(f"Worker received request from {peer} for rows {start}:{end}")

        # compute row-by-row and log detailed operations
        partial_rows = []
        for global_row in range(start, end):
            a_row = A[global_row]
            row_result = []
            print(f"Worker computing row {global_row}: A_row = {a_row.tolist()}")
            for j in range(B.shape[1]):
                b_col = B[:, j]
                value = float(np.dot(a_row, b_col))
                row_result.append(value)
                print(f"  -> element ({global_row},{j}) = dot(A_row, B_col) = dot({a_row.tolist()}, {b_col.tolist()}) = {value}")
            partial_rows.append(row_result)

        partial = np.array(partial_rows)
        print(f"Worker completed rows {start}:{end}, partial result:\n{partial}")

        return matrix_pb2.MatrixReply(
            result=partial.flatten().tolist(),
            rows=partial.shape[0],
            cols=partial.shape[1]
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
