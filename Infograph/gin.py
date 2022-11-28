from sklearn import preprocessing
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import GridSearchCV, KFold, StratifiedKFold
from sklearn.model_selection import cross_val_score
from sklearn.svm import SVC, LinearSVC
from torch.nn import Sequential, Linear, ReLU
from torch_geometric.data import DataLoader
from torch_geometric.datasets import TUDataset
from torch_geometric.nn import GINConv, global_add_pool
from tqdm import tqdm
import numpy as np
import os.path as osp
import sys
import torch
import torch.nn.functional as F
import torch.nn as nn
import torch_geometric


class Encoder(torch.nn.Module):
    def __init__(self, num_features, dim, num_gc_layers):
        super(Encoder, self).__init__()

        # num_features = dataset.num_features
        # dim = 32
        self.num_gc_layers = num_gc_layers

        # self.nns = []
        self.convs = torch.nn.ModuleList()
        self.bns = torch.nn.ModuleList()

        for i in range(num_gc_layers):

            if i:
                nn = Sequential(Linear(dim, dim), ReLU(), Linear(dim, dim))
            else:
                nn = Sequential(Linear(num_features, dim), ReLU(), Linear(dim, dim))
            conv = GINConv(nn)
            bn = torch.nn.BatchNorm1d(dim)

            self.convs.append(conv)
            self.bns.append(bn)


    def forward(self, x, edge_index, batch):
        if x is None:
            x = torch.ones((batch.shape[0], 1)).to(device)

        xs = []
        for i in range(self.num_gc_layers):

            x = F.relu(self.convs[i](x, edge_index))
            x = self.bns[i](x)
            xs.append(x)
            # if i == 2:
                # feature_map = x2

        xpool = [global_add_pool(x, batch) for x in xs]
        x = torch.cat(xpool, 1)
        return x, torch.cat(xs, 1)

    def get_embeddings(self, loader):

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        ret = []
        y = []
        with torch.no_grad():
            for data in loader:
                data.to(device)
                x, edge_index, batch = data.x, data.edge_index, data.batch
                if x is None:
                    x = torch.ones((batch.shape[0],1)).to(device)
                x, _ = self.forward(x, edge_index, batch)
                ret.append(x.cpu().numpy())
                y.append(data.y.cpu().numpy())
        ret = np.concatenate(ret, 0)
        y = np.concatenate(y, 0)
        return ret, y

class GNN_Infograph(nn.Module):
    """
    GNN with neural network for readout
    """
    def __init__(self, in_channel, hidden_channel, out_channel, num_gc_layers, batch_norm=False, **kwargs):
        super(GNN_Infograph, self).__init__()

        self.num_gc_layers = num_gc_layers

        self.convs = torch.nn.ModuleList()
        self.bns = torch.nn.ModuleList()
        self.is_batch_norm = batch_norm

        for i in range(num_gc_layers):

            if i:
                nn = Sequential(Linear(hidden_channel, hidden_channel), ReLU(), Linear(hidden_channel, hidden_channel))
            else:
                nn = Sequential(Linear(in_channel, hidden_channel), ReLU(), Linear(hidden_channel, hidden_channel))
            conv = GINConv(nn, train_eps=True)
            bn = torch.nn.BatchNorm1d(hidden_channel)

            self.convs.append(conv)
            self.bns.append(bn)

        #Final layer applying network
        # self.embedder = Linear(hidden_channel*self.num_gc_layers,out_channel)
        # self.embedder = Linear(hidden_channel,out_channel)


    def forward(self, x, edge_index, batch, return_rep=False):
        xs = []
        outputs = []
        for i in range(self.num_gc_layers):

            x = F.relu(self.convs[i](x, edge_index))
            if self.is_batch_norm:
                x = self.bns[i](x)
            xs.append(x)
            if return_rep:
                outputs.append(torch_geometric.nn.global_mean_pool(x,batch))
            # if i == 2:
                # feature_map = x2

        xpool = [torch_geometric.nn.global_mean_pool(x, batch) for x in xs]
        x = torch.cat(xpool, 1)
        if return_rep:
            return x, torch.cat(xs,1), outputs
        else:
            return x, torch.cat(xs, 1)
        # xpool = [torch_geometric.nn.global_mean_pool(x, batch) for x in xs]
        # x = torch.cat(xpool, 1)
        # x = torch_geometric.nn.global_mean_pool(x,batch)
        # x = self.embedder(x)
        # if return_rep:
        #     outputs.append(x)
        #     return x, outputs
        # else:
        #     return x

    def get_embeddings(self, loader):

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        ret = []
        y = []
        X = []
        Y = []
        H = {}
        flag = 0
        with torch.no_grad():
            for data in loader:
                x_input = torch_geometric.nn.global_mean_pool(data.x,data.batch)
                X.append(x_input)
                Y.append(data.y)
                data.to(device)
                x, edge_index, batch = data.x, data.edge_index, data.batch
                if x is None:
                    x = torch.ones((batch.shape[0],1)).to(device)
                x, _, outputs = self.forward(x, edge_index, batch, return_rep=True)
                for i,output in enumerate(outputs):
                    if flag==0:
                        H[i] = []
                    H[i].append(output.detach().cpu()) 
                flag = 1
                ret.append(x.cpu().numpy())
                y.append(data.y.cpu().numpy())
        ret = np.concatenate(ret, 0)
        y = np.concatenate(y, 0)
        X = torch.cat(X)
        Y = torch.cat(Y)
        for key in H.keys():    
            H[key] = torch.cat(H[key])
            H[key] = H[key].numpy()
        IB_info = (X.numpy(),Y.numpy(),H) 
        return ret, y, IB_info    

class Net(torch.nn.Module):
    def __init__(self):
        super(Net, self).__init__()

        try:
            num_features = dataset.num_features
        except:
            num_features = 1
        dim = 32

        self.encoder = Encoder(num_features, dim)

        self.fc1 = Linear(dim*5, dim)
        self.fc2 = Linear(dim, dataset.num_classes)

    def forward(self, x, edge_index, batch):
        if x is None:
            x = torch.ones(batch.shape[0]).to(device)

        x, _ = self.encoder(x, edge_index, batch)
        x = F.relu(self.fc1(x))
        x = F.dropout(x, p=0.5, training=self.training)
        x = self.fc2(x)
        return F.log_softmax(x, dim=-1)

def train(epoch):
    model.train()

    if epoch == 51:
        for param_group in optimizer.param_groups:
            param_group['lr'] = 0.5 * param_group['lr']

    loss_all = 0
    for data in train_loader:
        data = data.to(device)
        optimizer.zero_grad()
        # print(data.x.shape)
        # [ num_nodes x num_node_labels ]
        # print(data.edge_index.shape)
        #  [2 x num_edges ]
        # print(data.batch.shape)
        # [ num_nodes ]
        output = model(data.x, data.edge_index, data.batch)
        loss = F.nll_loss(output, data.y)
        loss.backward()
        loss_all += loss.item() * data.num_graphs
        optimizer.step()

    return loss_all / len(train_dataset)

def test(loader):
    model.eval()

    correct = 0
    for data in loader:
        data = data.to(device)
        output = model(data.x, data.edge_index, data.batch)
        pred = output.max(dim=1)[1]
        correct += pred.eq(data.y).sum().item()
    return correct / len(loader.dataset)


if __name__ == '__main__':
    for percentage in [ 1.]:
        for DS in [sys.argv[1]]:
            if 'REDDIT' in DS:
                epochs = 200
            else:
                epochs = 100
            path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data', DS)
            accuracies = [[] for i in range(epochs)]
            #kf = StratifiedKFold(n_splits=10, shuffle=True, random_state=None)
            dataset = TUDataset(path, name=DS) #.shuffle()
            num_graphs = len(dataset)
            print('Number of graphs', len(dataset))
            dataset = dataset[:int(num_graphs * percentage)]
            dataset = dataset.shuffle()

            kf = KFold(n_splits=10, shuffle=True, random_state=None)
            for train_index, test_index in kf.split(dataset):

                # x_train, x_test = x[train_index], x[test_index]
                # y_train, y_test = y[train_index], y[test_index]
                train_dataset = [dataset[int(i)] for i in list(train_index)]
                test_dataset = [dataset[int(i)] for i in list(test_index)]
                print('len(train_dataset)', len(train_dataset))
                print('len(test_dataset)', len(test_dataset))

                train_loader = DataLoader(train_dataset, batch_size=128)
                test_loader = DataLoader(test_dataset, batch_size=128)
                # print('train', len(train_loader))
                # print('test', len(test_loader))

                device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
                model = Net().to(device)
                optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

                for epoch in range(1, epochs+1):
                    train_loss = train(epoch)
                    train_acc = test(train_loader)
                    test_acc = test(test_loader)
                    accuracies[epoch-1].append(test_acc)
                    tqdm.write('Epoch: {:03d}, Train Loss: {:.7f}, '
                          'Train Acc: {:.7f}, Test Acc: {:.7f}'.format(epoch, train_loss,
                                                                       train_acc, test_acc))
            tmp = np.mean(accuracies, axis=1)
            print(percentage, DS, np.argmax(tmp), np.max(tmp), np.std(accuracies[np.argmax(tmp)]))
            input()
