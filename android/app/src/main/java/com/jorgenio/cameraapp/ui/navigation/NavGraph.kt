package com.jorgenio.cameraapp.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.jorgenio.cameraapp.ui.screens.cameras.CamerasListScreen
import com.jorgenio.cameraapp.ui.screens.events.EventsScreen
import com.jorgenio.cameraapp.ui.screens.login.LoginScreen
import com.jorgenio.cameraapp.ui.screens.player.PlayerScreen
import com.jorgenio.cameraapp.ui.screens.recordings.RecordingsScreen
import com.jorgenio.cameraapp.ui.screens.settings.SettingsScreen

sealed class Screen(val route: String) {
    data object Login : Screen("login")
    data object Cameras : Screen("cameras")
    data object Events : Screen("events")
    data object Recordings : Screen("recordings")
    data object Settings : Screen("settings")
    data object Player : Screen("player/{cameraId}/{cameraName}") {
        fun createRoute(id: String, name: String): String {
            val safeName = java.net.URLEncoder.encode(name, "UTF-8")
            return "player/${java.net.URLEncoder.encode(id, "UTF-8")}/$safeName"
        }
    }
}

@Composable
fun NavGraph() {
    val navController = rememberNavController()

    NavHost(navController = navController, startDestination = Screen.Login.route) {
        composable(Screen.Login.route) {
            LoginScreen(
                onLoginSuccess = {
                    navController.navigate(Screen.Cameras.route) {
                        popUpTo(Screen.Login.route) { inclusive = true }
                    }
                }
            )
        }

        composable(Screen.Cameras.route) {
            CamerasListScreen(
                onCameraClick = { cam ->
                    navController.navigate(Screen.Player.createRoute(cam.id, cam.name))
                },
                onOpenEvents = { navController.navigate(Screen.Events.route) },
                onOpenRecordings = { navController.navigate(Screen.Recordings.route) },
                onOpenSettings = { navController.navigate(Screen.Settings.route) }
            )
        }

        composable(
            route = Screen.Player.route,
            arguments = listOf(
                navArgument("cameraId") { type = NavType.StringType },
                navArgument("cameraName") { type = NavType.StringType }
            )
        ) { entry ->
            val cameraId = java.net.URLDecoder.decode(
                entry.arguments?.getString("cameraId") ?: "", "UTF-8"
            )
            val cameraName = java.net.URLDecoder.decode(
                entry.arguments?.getString("cameraName") ?: "", "UTF-8"
            )
            PlayerScreen(
                cameraId = cameraId,
                cameraName = cameraName,
                onBack = { navController.popBackStack() }
            )
        }

        composable(Screen.Events.route) {
            EventsScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.Recordings.route) {
            RecordingsScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.Settings.route) {
            SettingsScreen(
                onBack = { navController.popBackStack() },
                onDisconnect = {
                    navController.navigate(Screen.Login.route) {
                        popUpTo(0) { inclusive = true }
                    }
                }
            )
        }
    }
}
