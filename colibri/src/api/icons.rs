use crate::api::{constants::QUERY_ICONS_TASK_PREFIX, AppState};
use crate::icons;
use axum::{extract::Query, extract::State, response::IntoResponse};
use log::error;
use reqwest::StatusCode;
use serde::Deserialize;
use std::{sync::Arc, time::SystemTime};
use tokio::fs;

const HOUR_IN_SECS: u64 = 60 * 60;
const MAX_ICON_RECHECK_PERIOD: u64 = HOUR_IN_SECS * 12;

/// Used when requesting an asset locally
#[derive(Deserialize)]
pub struct AssetIconRequest {
    // id of the asset to be queried
    asset_id: String,
    // hash used to inform the consumer if the file has changed locally or not
    match_header: Option<String>,
    #[serde(default)]
    use_collection_icon: bool, // default is false
}

/// Used when checking the state of an icon locally
#[derive(Deserialize)]
pub struct AssetIconCheck {
    // id of the asset to be queried
    asset_id: String,
    // true if we should delete the local file and pull it again
    force_refresh: Option<bool>,
    #[serde(default)]
    use_collection_icon: bool,
}

/// The handler for the get icon endpoint
///
/// Gets the given icon from the user's system if it's already
/// downloaded or asks for it from the icon sources. Returns it
/// if found and a 404 if not
pub async fn get_icon(
    State(state): State<Arc<AppState>>,
    Query(payload): Query<AssetIconRequest>,
) -> impl IntoResponse {
    let path = icons::get_asset_path(
        &payload.asset_id,
        state.data_dir.as_path(),
        payload.use_collection_icon,
        state.globaldb.as_ref(),
    )
    .await;

    match icons::get_icon(
        state.data_dir.clone(),
        &payload.asset_id,
        payload.match_header,
        path,
        state.globaldb.as_ref(),
    )
    .await
    {
        (status, Some(headers), Some(bytes)) => (status, headers, bytes).into_response(),
        (status, Some(headers), None) => (status, headers).into_response(),
        (status, _, _) => status.into_response(),
    }
}

/// The handler for the HEAD icon endpoint
///
/// First check if the file exists locally. If the file is not empty it means
/// that we have the image locally and we can serve it. Otherwise if the file
/// has 0 size it means that the last time it was queried the icon couldn't be
/// found remotely. Additionally if that query was more than MAX_ICON_RECHECK_PERIOD
/// hours ago we will retry. If on the other hand we queried it less than
/// MAX_ICON_RECHECK_PERIOD hours ago then we treat it as if the icon doesn't exist.
///
/// If force_refresh is set to true then we ignore the local file and force a query
/// of the icon.
pub async fn check_icon(
    State(state): State<Arc<AppState>>,
    Query(payload): Query<AssetIconCheck>,
) -> impl IntoResponse {
    let path = icons::get_asset_path(
        &payload.asset_id,
        state.data_dir.as_path(),
        payload.use_collection_icon,
        state.globaldb.as_ref(),
    )
    .await;

    if let Some(found_path) = icons::find_icon(state.data_dir.as_path(), &path, &payload.asset_id).await
    {
        return match fs::metadata(found_path.clone()).await {
            Ok(metadata) => {
                if metadata.len() > 0 {
                    handle_non_empty_icon(
                        state,
                        payload.asset_id,
                        path,
                        found_path,
                        payload.force_refresh,
                    )
                    .await
                    .into_response()
                } else if let Some(underlying_id) =
                    get_underlying_id(&state, &payload.asset_id).await
                {
                    let underlying_path = icons::get_asset_path(
                        &underlying_id,
                        state.data_dir.as_path(),
                        false,
                        state.globaldb.as_ref(),
                    )
                    .await;
                    check_icon_for_asset_id(
                        state,
                        underlying_id,
                        underlying_path,
                        payload.force_refresh,
                    )
                    .await
                    .into_response()
                } else {
                    handle_empty_icon(
                        state,
                        payload.asset_id,
                        path,
                        found_path,
                        metadata,
                    )
                    .await
                    .into_response()
                }
            }
            Err(e) => {
                error!("Failed to query icon for {} due to {}", payload.asset_id, e);
                StatusCode::NOT_FOUND.into_response()
            }
        };
    }

    if let Some(underlying_id) = get_underlying_id(&state, &payload.asset_id).await {
        let underlying_path = icons::get_asset_path(
            &underlying_id,
            state.data_dir.as_path(),
            false,
            state.globaldb.as_ref(),
        )
        .await;
        return check_icon_for_asset_id(
            state,
            underlying_id,
            underlying_path,
            payload.force_refresh,
        )
        .await
        .into_response();
    }

    // There is no local reference to the file, query it. Ensure that if it is requested
    // again only one task handles it.
    query_icon_from_payload(state, payload, path)
        .await
        .into_response()
}

/// Helper function to update the status of the query in the shared state
/// and start the query of an icon remotely.
async fn query_icon_from_payload(
    state: Arc<AppState>,
    payload: AssetIconCheck,
    path: std::path::PathBuf,
) -> StatusCode {
    query_icon(state, payload.asset_id, path).await
}

async fn query_icon(state: Arc<AppState>, asset_id: String, path: std::path::PathBuf) -> StatusCode {
    let task_name = format!("{}_{}", QUERY_ICONS_TASK_PREFIX, asset_id);
    let mut tasks_guard = state.active_tasks.lock().await;
    if !tasks_guard.insert(task_name.clone()) {
        return StatusCode::ACCEPTED;
    };
    drop(tasks_guard); // this drop releases the mutex guard allowing other tasks to acquire it.

    tokio::spawn({
        let active_tasks = state.active_tasks.clone();
        let task_key = task_name.clone();
        async move {
            icons::query_icon_remotely(
                asset_id,
                path,
                state.coingecko.clone(),
                state.evm_manager.clone(),
            )
            .await;
            active_tasks.lock().await.remove(&task_key);
        }
    });
    StatusCode::ACCEPTED
}

async fn handle_non_empty_icon(
    state: Arc<AppState>,
    asset_id: String,
    path: std::path::PathBuf,
    found_path: std::path::PathBuf,
    force_refresh: Option<bool>,
) -> StatusCode {
    if force_refresh != Some(true) {
        return StatusCode::OK;
    }

    if let Err(error) = fs::remove_file(found_path).await {
        error!(
            "Failed to delete file {} when force refresh was set due to {}",
            path.display(),
            error,
        );
        return StatusCode::INTERNAL_SERVER_ERROR;
    }

    query_icon(state, asset_id, path).await
}

async fn handle_empty_icon(
    state: Arc<AppState>,
    asset_id: String,
    path: std::path::PathBuf,
    found_path: std::path::PathBuf,
    metadata: std::fs::Metadata,
) -> StatusCode {
    let Ok(time) = metadata.modified() else {
        // This shouldn't happen as we ship to platforms with metadata on files
        error!("Platform doesn't support last modified ts");
        return StatusCode::INTERNAL_SERVER_ERROR;
    };

    let Ok(duration) = SystemTime::now().duration_since(time) else {
        error!("Error calculating elapsed duration");
        return StatusCode::INTERNAL_SERVER_ERROR;
    };

    if duration.as_secs() < MAX_ICON_RECHECK_PERIOD {
        // It was queried recently so don't try again
        return StatusCode::NOT_FOUND;
    }

    // Since we tried long ago enough retry again
    let _ = fs::remove_file(found_path).await;
    tokio::spawn(icons::query_icon_remotely(
        asset_id,
        path,
        state.coingecko.clone(),
        state.evm_manager.clone(),
    ));
    StatusCode::ACCEPTED
}

async fn check_icon_for_asset_id(
    state: Arc<AppState>,
    asset_id: String,
    path: std::path::PathBuf,
    force_refresh: Option<bool>,
) -> StatusCode {
    let Some(found_path) = icons::find_icon(state.data_dir.as_path(), &path, &asset_id).await else {
        return query_icon(state, asset_id, path).await;
    };
    let metadata = match fs::metadata(found_path.clone()).await {
        Ok(m) => m,
        Err(e) => {
            error!("Failed to query icon for {} due to {}", asset_id, e);
            return StatusCode::NOT_FOUND;
        }
    };
    if metadata.len() > 0 {
        handle_non_empty_icon(state, asset_id, path, found_path, force_refresh).await
    } else {
        handle_empty_icon(state, asset_id, path, found_path, metadata).await
    }
}

async fn get_underlying_id(state: &Arc<AppState>, asset_id: &str) -> Option<String> {
    match state
        .globaldb
        .get_single_underlying_token_with_protocol(asset_id)
        .await
    {
        Ok(result) => result,
        Err(e) => {
            error!(
                "Failed to query underlying token for {} due to {}",
                asset_id, e
            );
            None
        }
    }
}
