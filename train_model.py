"""
Train a Bidirectional LSTM for weight prediction from nutrition data.

Research-backed approach (NIH papers):
- Bidirectional LSTM captures both past and future nutritional context
- Dropout + L2 regularization to prevent overfitting
- 15-day sliding window: input = 15 days of [cal, protein, fat, carbs, fiber, water]
- Output: predicted weight_change for the next day

Architecture:
  Input (15, 6) → BiLSTM(64) → BiLSTM(32) → Dense(32) → ReLU → Dropout → Dense(1)

Usage: python train_model.py
"""
import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
import joblib

# -- Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, 'data', 'weight_tracking_cleaned.csv')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
os.makedirs(MODEL_DIR, exist_ok=True)

# -- Config
SEQ_LEN = 15
FEATURE_COLS = ['calorie_intake', 'tdee', 'caloric_surplus']
TARGET_COL = 'weight_change'
BATCH_SIZE = 256
EPOCHS = 15
PATIENCE = 15
LR = 0.001

# ======================================================================
#  Bidirectional LSTM Model
# ======================================================================
class NutritionLSTM(nn.Module):
    """
    Bidirectional LSTM for weight change prediction.
    Research shows BiLSTM captures temporal patterns better for health data.
    """
    def __init__(self, input_size, hidden_size=64, num_layers=2, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
            bidirectional=True  # Bidirectional for better temporal modeling
        )
        # BiLSTM outputs hidden_size * 2 (forward + backward)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size * 2, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Dropout(dropout * 0.5),
            nn.Linear(16, 1)
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        # Take the last time step output
        last_hidden = lstm_out[:, -1, :]
        return self.fc(last_hidden)


def create_sequences(features, targets, seq_len):
    """Create sliding window sequences from time series data."""
    X, y = [], []
    for i in range(len(features) - seq_len):
        X.append(features[i:i + seq_len])
        y.append(targets[i + seq_len])
    return np.array(X), np.array(y)


def main():
    print("[1/5] Loading dataset...")
    df = pd.read_csv(DATA_PATH)
    print(f"  {len(df)} rows loaded")

    # -- Prepare features and target
    features = df[FEATURE_COLS].values.astype(np.float32)
    targets = df[TARGET_COL].values.reshape(-1, 1).astype(np.float32)

    # -- Scale data
    feature_scaler = StandardScaler()
    target_scaler = StandardScaler()
    features_scaled = feature_scaler.fit_transform(features)
    targets_scaled = target_scaler.fit_transform(targets)

    print("[2/5] Preprocessing...")
    X, y = create_sequences(features_scaled, targets_scaled, SEQ_LEN)
    print(f"  X shape: {X.shape}  |  y shape: {y.shape}")

    # -- Train/Val/Test split (70/15/15)
    n = len(X)
    n_train = int(n * 0.70)
    n_val = int(n * 0.85)

    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = X[n_train:n_val], y[n_train:n_val]
    X_test, y_test = X[n_val:], y[n_val:]

    # -- DataLoaders
    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test))

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)
    test_loader = DataLoader(test_ds, batch_size=BATCH_SIZE)

    # -- Model
    print("[3/5] Building Bidirectional LSTM model...")
    input_size = len(FEATURE_COLS)
    hidden_size = 64
    num_layers = 2
    model = NutritionLSTM(input_size=input_size, hidden_size=hidden_size,
                           num_layers=num_layers, dropout=0.3)
    print(model)

    criterion = nn.SmoothL1Loss()  # Huber loss - more robust than MSE
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    # -- Training
    print(f"\n[4/5] Training for up to {EPOCHS} epochs (patience={PATIENCE})...")
    best_val_loss = float('inf')
    patience_counter = 0
    best_state = None

    for epoch in range(1, EPOCHS + 1):
        model.train()
        train_loss = 0
        for xb, yb in train_loader:
            pred = model(xb)
            loss = criterion(pred, yb)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)  # Gradient clipping
            optimizer.step()
            train_loss += loss.item() * len(xb)
        train_loss /= len(train_ds)

        # Validation
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for xb, yb in val_loader:
                pred = model(xb)
                val_loss += criterion(pred, yb).item() * len(xb)
        val_loss /= len(val_ds)

        scheduler.step(val_loss)

        if epoch <= 5 or epoch % 5 == 0 or val_loss < best_val_loss:
            print(f"  Epoch {epoch:3d}/{EPOCHS}  |  Train: {train_loss:.6f}  |  Val: {val_loss:.6f}  |  LR: {optimizer.param_groups[0]['lr']:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            best_state = model.state_dict().copy()
        else:
            patience_counter += 1

        if patience_counter >= PATIENCE:
            print(f"\n  Early stopping at epoch {epoch}")
            break

    # -- Test
    model.load_state_dict(best_state)
    model.eval()
    test_preds, test_actuals = [], []
    with torch.no_grad():
        for xb, yb in test_loader:
            pred = model(xb)
            test_preds.append(pred.numpy())
            test_actuals.append(yb.numpy())

    test_preds = np.vstack(test_preds)
    test_actuals = np.vstack(test_actuals)

    # Inverse scale
    pred_real = target_scaler.inverse_transform(test_preds)
    actual_real = target_scaler.inverse_transform(test_actuals)

    mae = np.mean(np.abs(pred_real - actual_real))
    rmse = np.sqrt(np.mean((pred_real - actual_real) ** 2))
    print(f"\n  Best Val Loss: {best_val_loss:.6f}")
    print(f"  Test MAE:  {mae:.4f} kg")
    print(f"  Test RMSE: {rmse:.4f} kg")

    # -- Save
    print("[5/5] Saving model and scalers...")
    model.load_state_dict(best_state)
    torch.save({
        'model_state_dict': best_state,
        'input_size': input_size,
        'hidden_size': hidden_size,
        'num_layers': num_layers,
        'feature_cols': FEATURE_COLS,
        'seq_len': SEQ_LEN,
        'mae': mae,
        'rmse': rmse,
    }, os.path.join(MODEL_DIR, 'lstm_model.pth'))

    joblib.dump(feature_scaler, os.path.join(MODEL_DIR, 'feature_scaler.pkl'))
    joblib.dump(target_scaler, os.path.join(MODEL_DIR, 'target_scaler.pkl'))

    print(f"\n  Model saved -> {os.path.join(MODEL_DIR, 'lstm_model.pth')}")
    print(f"  Feature scaler -> {os.path.join(MODEL_DIR, 'feature_scaler.pkl')}")
    print(f"  Target scaler  -> {os.path.join(MODEL_DIR, 'target_scaler.pkl')}")
    print("\nDone! Run the Flask app now.")


if __name__ == '__main__':
    main()
