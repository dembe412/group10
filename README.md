Distributed Matrix Multiplication (gRPC)

Run a coordinator (client) that distributes rows of a matrix multiplication to multiple worker servers.

Requirements

pip install -r requirements.txt

Generate gRPC stubs

python generate_grpc.py

Start workers (each in its own terminal)

# start first worker on 50051
python worker.py --port 50051

# start second worker on 50052
python worker.py --port 50052

# start third worker on 50053
python worker.py --port 50053

Run coordinator (you can pass worker list)

python coordinator.py --workers "localhost:50051,localhost:50052,localhost:50053"

The coordinator will prompt to use random matrices or manual input. The coordinator is fault tolerant: if a worker is unreachable the coordinator will retry and reassign its chunk to other available workers.
