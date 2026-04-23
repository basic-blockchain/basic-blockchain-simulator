from flask import Flask, jsonify
from domain import BlockchainService


app = Flask(__name__)
blockchain = BlockchainService()

# Home route
@app.route('/', methods=['GET'])
def home():
    response = {
        'message': 'Blockchain simulator is running',
        'routes': {
            'mine_block': '/mine_block',
            'get_chain': '/get_chain',
            'valid': '/valid'
        }
    }
    return jsonify(response), 200


# Mining a new block


@app.route('/mine_block', methods=['GET'])
def mine_block():
    previous_block = blockchain.previous_block()
    previous_proof = previous_block.proof
    proof = blockchain.proof_of_work(previous_proof)
    previous_hash = blockchain.hash_block(previous_block)
    block = blockchain.create_block(proof, previous_hash)

    response = {'message': 'A block is MINED',
                'index': block.index,
                'timestamp': block.timestamp,
                'proof': block.proof,
                'previous_hash': block.previous_hash}

    return jsonify(response), 200

# Display blockchain in json format


@app.route('/get_chain', methods=['GET'])
def display_chain():
    chain = blockchain.chain_as_dicts()
    response = {'chain': chain,
                'length': len(chain)}
    return jsonify(response), 200

# Check validity of blockchain


@app.route('/valid', methods=['GET'])
def valid():
    valid = blockchain.is_chain_valid()

    if valid:
        response = {'message': 'The Blockchain is valid.'}
    else:
        response = {'message': 'The Blockchain is not valid.'}
    return jsonify(response), 200


# Run the flask server locally
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)