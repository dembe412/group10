import grpc
import numpy as np
import matrix_pb2
import matrix_pb2_grpc


workers = [
    "localhost:50051",
    # "localhost:50052",
    "localhost:50053"
]


def compute_distributed(A, B):

    rows = A.shape[0]
    chunk = rows // len(workers)

    results = []
    start = 0

    for i, address in enumerate(workers):

        end = start + chunk
        if i == len(workers) - 1:
            end = rows

        channel = grpc.insecure_channel(address)
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

        response = stub.ComputeRows(request)

        part = np.array(response.result).reshape(
            response.rows, response.cols
        )

        results.append(part)
        start = end

    return np.vstack(results)


if __name__ == "__main__":

    A = np.random.randint(1, 10, (2, 2))
    B = np.random.randint(1, 10, (2, 2))

    print("Matrix A:\n", A)
    print("Matrix B:\n", B)

    C = compute_distributed(A, B)

    print("\nDistributed Result:\n", C)
