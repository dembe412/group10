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

        partial = A[start:end] @ B

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
    server.wait_for_termination()


if __name__ == "__main__":
    serve(50053)
