-- MySQL dump 10.13  Distrib 8.4.13, for Linux (x86_64)
--
-- Host: localhost    Database: django_db_name
-- ------------------------------------------------------
-- Server version	8.4.13

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `auth_group`
--

DROP TABLE IF EXISTS `auth_group`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_group` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `auth_group`
--

LOCK TABLES `auth_group` WRITE;
/*!40000 ALTER TABLE `auth_group` DISABLE KEYS */;
/*!40000 ALTER TABLE `auth_group` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `auth_group_permissions`
--

DROP TABLE IF EXISTS `auth_group_permissions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_group_permissions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `group_id` int NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_group_permissions_group_id_permission_id_0cd325b0_uniq` (`group_id`,`permission_id`),
  KEY `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` (`permission_id`),
  CONSTRAINT `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `auth_group_permissions_group_id_b120cbf9_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `auth_group_permissions`
--

LOCK TABLES `auth_group_permissions` WRITE;
/*!40000 ALTER TABLE `auth_group_permissions` DISABLE KEYS */;
/*!40000 ALTER TABLE `auth_group_permissions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `auth_permission`
--

DROP TABLE IF EXISTS `auth_permission`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_permission` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `content_type_id` int NOT NULL,
  `codename` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_permission_content_type_id_codename_01ab375a_uniq` (`content_type_id`,`codename`),
  CONSTRAINT `auth_permission_content_type_id_2f476e4b_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=61 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `auth_permission`
--

LOCK TABLES `auth_permission` WRITE;
/*!40000 ALTER TABLE `auth_permission` DISABLE KEYS */;
INSERT INTO `auth_permission` VALUES (1,'Can add log entry',1,'add_logentry'),(2,'Can change log entry',1,'change_logentry'),(3,'Can delete log entry',1,'delete_logentry'),(4,'Can view log entry',1,'view_logentry'),(5,'Can add permission',3,'add_permission'),(6,'Can change permission',3,'change_permission'),(7,'Can delete permission',3,'delete_permission'),(8,'Can view permission',3,'view_permission'),(9,'Can add group',2,'add_group'),(10,'Can change group',2,'change_group'),(11,'Can delete group',2,'delete_group'),(12,'Can view group',2,'view_group'),(13,'Can add content type',4,'add_contenttype'),(14,'Can change content type',4,'change_contenttype'),(15,'Can delete content type',4,'delete_contenttype'),(16,'Can view content type',4,'view_contenttype'),(17,'Can add session',5,'add_session'),(18,'Can change session',5,'change_session'),(19,'Can delete session',5,'delete_session'),(20,'Can view session',5,'view_session'),(21,'Can add category',7,'add_category'),(22,'Can change category',7,'change_category'),(23,'Can delete category',7,'delete_category'),(24,'Can view category',7,'view_category'),(25,'Can add page',9,'add_page'),(26,'Can change page',9,'change_page'),(27,'Can delete page',9,'delete_page'),(28,'Can view page',9,'view_page'),(29,'Can add user',8,'add_customuser'),(30,'Can change user',8,'change_customuser'),(31,'Can delete user',8,'delete_customuser'),(32,'Can view user',8,'view_customuser'),(33,'Can add artikel',6,'add_artikel'),(34,'Can change artikel',6,'change_artikel'),(35,'Can delete artikel',6,'delete_artikel'),(36,'Can view artikel',6,'view_artikel'),(37,'Can add product',10,'add_product'),(38,'Can change product',10,'change_product'),(39,'Can delete product',10,'delete_product'),(40,'Can view product',10,'view_product'),(41,'Can add booking cek unit',11,'add_bookingcekunit'),(42,'Can change booking cek unit',11,'change_bookingcekunit'),(43,'Can delete booking cek unit',11,'delete_bookingcekunit'),(44,'Can view booking cek unit',11,'view_bookingcekunit'),(45,'Can add foto mobil',12,'add_fotomobil'),(46,'Can change foto mobil',12,'change_fotomobil'),(47,'Can delete foto mobil',12,'delete_fotomobil'),(48,'Can view foto mobil',12,'view_fotomobil'),(49,'Can add produk',13,'add_produk'),(50,'Can change produk',13,'change_produk'),(51,'Can delete produk',13,'delete_produk'),(52,'Can view produk',13,'view_produk'),(53,'Can add titip jual',14,'add_titipjual'),(54,'Can change titip jual',14,'change_titipjual'),(55,'Can delete titip jual',14,'delete_titipjual'),(56,'Can view titip jual',14,'view_titipjual'),(57,'Can add transaksi',15,'add_transaksi'),(58,'Can change transaksi',15,'change_transaksi'),(59,'Can delete transaksi',15,'delete_transaksi'),(60,'Can view transaksi',15,'view_transaksi');
/*!40000 ALTER TABLE `auth_permission` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `django_admin_log`
--

DROP TABLE IF EXISTS `django_admin_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_admin_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `action_time` datetime(6) NOT NULL,
  `object_id` longtext,
  `object_repr` varchar(200) NOT NULL,
  `action_flag` smallint unsigned NOT NULL,
  `change_message` longtext NOT NULL,
  `content_type_id` int DEFAULT NULL,
  `user_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `django_admin_log_content_type_id_c4bce8eb_fk_django_co` (`content_type_id`),
  KEY `django_admin_log_user_id_c564eba6_fk_toko_customuser_id` (`user_id`),
  CONSTRAINT `django_admin_log_content_type_id_c4bce8eb_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`),
  CONSTRAINT `django_admin_log_user_id_c564eba6_fk` FOREIGN KEY (`user_id`) REFERENCES `toko_customuser` (`id`),
  CONSTRAINT `django_admin_log_chk_1` CHECK ((`action_flag` >= 0))
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `django_admin_log`
--

LOCK TABLES `django_admin_log` WRITE;
/*!40000 ALTER TABLE `django_admin_log` DISABLE KEYS */;
INSERT INTO `django_admin_log` VALUES (1,'2026-10-21 08:41:19.441970','1','SUV',1,'[{\"added\": {}}]',7,1),(2,'2026-10-21 08:42:18.172982','1','The all-new PALISADE (2025)',1,'[{\"added\": {}}]',10,1),(3,'2026-10-21 16:41:30.478728','1','Hyundai Palisade 2.2 Signature AT (Ready Stock)',1,'[{\"added\": {}}]',13,1),(4,'2026-10-22 03:00:30.100263','1','Honda Jazz RS 2018 - Akmal (Proses Cek/Inspeksi)',1,'[{\"added\": {}}]',14,1),(5,'2026-10-22 03:02:08.981129','1','Honda Jazz RS 2018 - Akmal (Disetujui & Tayang)',2,'[{\"changed\": {\"fields\": [\"Status\"]}}]',14,1),(6,'2026-10-22 03:14:00.159167','1','Honda Jazz RS 2018 - Akmal (Disetujui & Tayang)',2,'[]',14,1),(7,'2026-10-29 01:46:22.174711','2','Toyota Avanza 1.3 G AT 2020 (Ready Stock)',1,'[{\"added\": {}}]',13,1),(8,'2026-10-29 01:51:27.037983','2','Toyota Avanza All New - Fawaz (Menunggu Verifikasi)',1,'[{\"added\": {}}]',14,1),(9,'2026-10-29 01:51:48.155501','2','Toyota Avanza All New - Fawaz (Menunggu Verifikasi)',2,'[]',14,1),(10,'2026-10-29 01:52:00.679061','2','Toyota Avanza All New - Fawaz (Disetujui & Tayang)',2,'[{\"changed\": {\"fields\": [\"Status\"]}}]',14,1);
/*!40000 ALTER TABLE `django_admin_log` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `django_content_type`
--

DROP TABLE IF EXISTS `django_content_type`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_content_type` (
  `id` int NOT NULL AUTO_INCREMENT,
  `app_label` varchar(100) NOT NULL,
  `model` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `django_content_type_app_label_model_76bd3d3b_uniq` (`app_label`,`model`)
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `django_content_type`
--

LOCK TABLES `django_content_type` WRITE;
/*!40000 ALTER TABLE `django_content_type` DISABLE KEYS */;
INSERT INTO `django_content_type` VALUES (1,'admin','logentry'),(2,'auth','group'),(3,'auth','permission'),(4,'contenttypes','contenttype'),(5,'sessions','session'),(6,'toko','artikel'),(11,'toko','bookingcekunit'),(7,'toko','category'),(8,'toko','customuser'),(12,'toko','fotomobil'),(9,'toko','page'),(10,'toko','product'),(13,'toko','produk'),(14,'toko','titipjual'),(15,'toko','transaksi');
/*!40000 ALTER TABLE `django_content_type` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `django_migrations`
--

DROP TABLE IF EXISTS `django_migrations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_migrations` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `app` varchar(255) NOT NULL,
  `name` varchar(255) NOT NULL,
  `applied` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=24 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `django_migrations`
--

LOCK TABLES `django_migrations` WRITE;
/*!40000 ALTER TABLE `django_migrations` DISABLE KEYS */;
INSERT INTO `django_migrations` VALUES (1,'contenttypes','0001_initial','2026-10-17 17:09:49.030226'),(2,'contenttypes','0002_remove_content_type_name','2026-10-17 17:09:49.227160'),(3,'auth','0001_initial','2026-10-17 17:09:49.688098'),(4,'auth','0002_alter_permission_name_max_length','2026-10-17 17:09:49.805420'),(5,'auth','0003_alter_user_email_max_length','2026-10-17 17:09:49.813987'),(6,'auth','0004_alter_user_username_opts','2026-10-17 17:09:49.825165'),(7,'auth','0005_alter_user_last_login_null','2026-10-17 17:09:49.836583'),(8,'auth','0006_require_contenttypes_0002','2026-10-17 17:09:49.841015'),(9,'auth','0007_alter_validators_add_error_messages','2026-10-17 17:09:49.849963'),(10,'auth','0008_alter_user_username_max_length','2026-10-17 17:09:49.861738'),(11,'auth','0009_alter_user_last_name_max_length','2026-10-17 17:09:49.872241'),(12,'auth','0010_alter_group_name_max_length','2026-10-17 17:09:49.898190'),(13,'auth','0011_update_proxy_permissions','2026-10-17 17:09:49.910233'),(14,'auth','0012_alter_user_first_name_max_length','2026-10-17 17:09:49.919734'),(15,'toko','0001_initial','2026-10-17 17:09:50.849694'),(16,'admin','0001_initial','2026-10-17 17:09:51.091158'),(17,'admin','0002_logentry_remove_auto_add','2026-10-17 17:09:51.106541'),(18,'admin','0003_logentry_add_action_flag_choices','2026-10-17 17:09:51.126309'),(19,'sessions','0001_initial','2026-10-17 17:09:51.188391'),(20,'toko','0002_product_bahan_bakar_product_image_product_kilometer_and_more','2026-10-21 08:27:40.141949'),(21,'toko','0003_bookingcekunit_fotomobil_produk_titipjual_transaksi_and_more','2026-10-21 16:33:02.989883'),(23,'toko','0004_customuser_foto_profil','2026-10-29 02:53:42.346057');
/*!40000 ALTER TABLE `django_migrations` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `django_session`
--

DROP TABLE IF EXISTS `django_session`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_session` (
  `session_key` varchar(40) NOT NULL,
  `session_data` longtext NOT NULL,
  `expire_date` datetime(6) NOT NULL,
  PRIMARY KEY (`session_key`),
  KEY `django_session_expire_date_a5c62663` (`expire_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `django_session`
--

LOCK TABLES `django_session` WRITE;
/*!40000 ALTER TABLE `django_session` DISABLE KEYS */;
INSERT INTO `django_session` VALUES ('e0mmf7blv983ajlss1zreix5wubg8g8f','.eJxVjEsOAiEQBe_C2pDm14BL956B9EArowaS-ayMdzcks9Dtq6r3Fon2raZ95SXNRZyFEqffbaL85DZAeVC7d5l725Z5kkORB13ltRd-XQ7376DSWket2bqAKpJ3FC0G1pM2aJ1HYnAERt9cKQUhB0ugPaIiZgMcIFpg8fkCwt43DQ:1xI7zu:yunO_wTwhaulbnUaHBx1rsHjndLq6CAXRWf97BXuj3Y','2026-10-31 17:15:06.954163'),('i49jk4e60y5blljtn1f6yblsyadzv0v5','.eJxVjEsOAiEQBe_C2pDm14BL956B9EArowaS-ayMdzcks9Dtq6r3Fon2raZ95SXNRZyFEqffbaL85DZAeVC7d5l725Z5kkORB13ltRd-XQ7376DSWket2bqAKpJ3FC0G1pM2aJ1HYnAERt9cKQUhB0ugPaIiZgMcIFpg8fkCwt43DQ:1xLt1h:DsDiF53cyS8X4TX-T5oxuCb25l2o2JwknaLpVgEhcck','2026-11-11 02:04:29.226186'),('oniiuva2d1lrcjwuti8oz0wip5ctzleu','.eJxVjEsOAiEQBe_C2pDm14BL956B9EArowaS-ayMdzcks9Dtq6r3Fon2raZ95SXNRZyFEqffbaL85DZAeVC7d5l725Z5kkORB13ltRd-XQ7376DSWket2bqAKpJ3FC0G1pM2aJ1HYnAERt9cKQUhB0ugPaIiZgMcIFpg8fkCwt43DQ:1xI7zw:-uRoa_HgF8e5P-aSxOjkqDxmFLKUiZbJoRZcxYyL8VI','2026-10-31 17:15:08.503055'),('v3m691hru9cns4lgtji76biafwktf4pv','.eJxVjDEOAiEQRe9CbQiDIGBp7xnIMAyyaiBZdivj3XWTLbT9773_EhHXpcZ18BynLM4CxOF3S0gPbhvId2y3Lqm3ZZ6S3BS50yGvPfPzsrt_BxVH_dbktEKjPJSTY6ZkFbJFCokzAvtgtAcLThldLAXABOxUoYJehyNpEu8P8gA4UQ:1xMKwP:b5GcBGQ4FRuGGyIhYUWBfERyUy4lvZkfW1TF5gIoD6g','2026-11-12 07:52:53.624388');
/*!40000 ALTER TABLE `django_session` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_bookingcekunit`
--

DROP TABLE IF EXISTS `toko_bookingcekunit`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_bookingcekunit` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `nama_pemesan` varchar(100) NOT NULL,
  `no_whatsapp` varchar(20) NOT NULL,
  `email` varchar(254) NOT NULL,
  `tanggal_cek_unit` date NOT NULL,
  `jam_cek_unit` time(6) NOT NULL,
  `opsi_delivery` varchar(10) NOT NULL,
  `alamat_pengantaran` longtext,
  `status` varchar(20) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `produk_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `toko_bookingcekunit_produk_id_f71aa387_fk_toko_produk_id` (`produk_id`),
  CONSTRAINT `toko_bookingcekunit_produk_id_f71aa387_fk` FOREIGN KEY (`produk_id`) REFERENCES `toko_produk` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_bookingcekunit`
--

LOCK TABLES `toko_bookingcekunit` WRITE;
/*!40000 ALTER TABLE `toko_bookingcekunit` DISABLE KEYS */;
/*!40000 ALTER TABLE `toko_bookingcekunit` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_customuser`
--

DROP TABLE IF EXISTS `toko_customuser`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_customuser` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `password` varchar(128) NOT NULL,
  `last_login` datetime(6) DEFAULT NULL,
  `is_superuser` tinyint(1) NOT NULL,
  `username` varchar(150) NOT NULL,
  `first_name` varchar(150) NOT NULL,
  `last_name` varchar(150) NOT NULL,
  `email` varchar(254) NOT NULL,
  `is_staff` tinyint(1) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `date_joined` datetime(6) NOT NULL,
  `alamat` longtext,
  `no_whatsapp` varchar(20) DEFAULT NULL,
  `foto_profil` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_customuser`
--

LOCK TABLES `toko_customuser` WRITE;
/*!40000 ALTER TABLE `toko_customuser` DISABLE KEYS */;
INSERT INTO `toko_customuser` VALUES (1,'pbkdf2_sha256$1000000$OJd7ZQSSekpDHfsZhLNVhe$Cy4PfCeI6AITOrnDm3QT/0ctuJMhqwcMoAj62F0iJIs=','2026-10-29 07:52:53.608437',1,'Akmal','Muhammad','Akmal','akmalmuh646@gmail.com',1,1,'2026-10-17 17:11:55.886987','Bulukumba','','profil/1/7b86dd2bf5314673a93983395ff9812c.webp'),(2,'pbkdf2_sha256$1500000$0foQCNhrta52ALU0vnJ051$03F/xt8JiPGoUnbZUwKvOPvrwE9awC5ompvRnGZ/abc=','2026-10-28 01:47:48.566706',0,'Akmalll','Akmal','','akmallll.123456@gmail.com',0,1,'2026-10-27 15:26:48.732289','Masagena','0852409595536',''),(3,'pbkdf2_sha256$1000000$hEg1E1Qvho3KwmWIT06ze9$fiLkF9coYbHIjHdVS9LUBaCIXN0ougLPOn8OqlNtUuY=','2026-10-29 03:56:34.997081',0,'Sandy','Sandy','','muhak09876@gmail.com',0,1,'2026-10-29 03:56:10.102213',NULL,'087724248309',''),(4,'pbkdf2_sha256$1000000$6Hu1B1gvklzc9mETKPp0fy$+/qP/5WA9seun+lrBQ48bAqsDPNf/kCuJbmk+cV+QwE=','2026-10-29 05:47:16.709993',0,'S4ndyy','Aswandy','','sandyaswandy@gmail.com',0,1,'2026-10-29 05:45:40.792034','','081354674151','profil/4/764c0516132a4f7cba6c973881d1d744.jpg');
/*!40000 ALTER TABLE `toko_customuser` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_customuser_groups`
--

DROP TABLE IF EXISTS `toko_customuser_groups`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_customuser_groups` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `customuser_id` bigint NOT NULL,
  `group_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `toko_customuser_groups_customuser_id_group_id_3cc4370a_uniq` (`customuser_id`,`group_id`),
  KEY `toko_customuser_groups_group_id_020b2541_fk_auth_group_id` (`group_id`),
  CONSTRAINT `toko_customuser_groups_customuser_id_ed0e1b2e_fk` FOREIGN KEY (`customuser_id`) REFERENCES `toko_customuser` (`id`),
  CONSTRAINT `toko_customuser_groups_group_id_020b2541_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_customuser_groups`
--

LOCK TABLES `toko_customuser_groups` WRITE;
/*!40000 ALTER TABLE `toko_customuser_groups` DISABLE KEYS */;
/*!40000 ALTER TABLE `toko_customuser_groups` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_customuser_user_permissions`
--

DROP TABLE IF EXISTS `toko_customuser_user_permissions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_customuser_user_permissions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `customuser_id` bigint NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `toko_customuser_user_per_customuser_id_permission_c20e7f92_uniq` (`customuser_id`,`permission_id`),
  KEY `toko_customuser_user_permission_id_5d612830_fk_auth_perm` (`permission_id`),
  CONSTRAINT `toko_customuser_user_permission_id_5d612830_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `toko_customuser_user_permissions_customuser_id_aacc38e1_fk` FOREIGN KEY (`customuser_id`) REFERENCES `toko_customuser` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_customuser_user_permissions`
--

LOCK TABLES `toko_customuser_user_permissions` WRITE;
/*!40000 ALTER TABLE `toko_customuser_user_permissions` DISABLE KEYS */;
/*!40000 ALTER TABLE `toko_customuser_user_permissions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_fotomobil`
--

DROP TABLE IF EXISTS `toko_fotomobil`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_fotomobil` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `gambar` varchar(100) NOT NULL,
  `keterangan` varchar(100) NOT NULL,
  `produk_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `toko_fotomobil_produk_id_80ae333d_fk_toko_produk_id` (`produk_id`),
  CONSTRAINT `toko_fotomobil_produk_id_80ae333d_fk` FOREIGN KEY (`produk_id`) REFERENCES `toko_produk` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_fotomobil`
--

LOCK TABLES `toko_fotomobil` WRITE;
/*!40000 ALTER TABLE `toko_fotomobil` DISABLE KEYS */;
/*!40000 ALTER TABLE `toko_fotomobil` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_produk`
--

DROP TABLE IF EXISTS `toko_produk`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_produk` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `sku` varchar(50) NOT NULL,
  `nama` varchar(200) NOT NULL,
  `merek` varchar(100) NOT NULL,
  `kategori` varchar(50) NOT NULL,
  `tahun` int NOT NULL,
  `transmisi` varchar(2) NOT NULL,
  `jarak_tempuh` int NOT NULL,
  `warna` varchar(50) NOT NULL,
  `lokasi` varchar(100) NOT NULL,
  `plat_nomor` varchar(20) NOT NULL,
  `harga` decimal(12,0) NOT NULL,
  `harga_promo` decimal(12,0) DEFAULT NULL,
  `tipe_penjual` varchar(3) NOT NULL,
  `status_stok` varchar(10) NOT NULL,
  `lulus_inspeksi` tinyint(1) NOT NULL,
  `ringkasan_inspeksi` longtext NOT NULL,
  `gambar_utama` varchar(100) NOT NULL,
  `deskripsi_singkat` longtext NOT NULL,
  `deskripsi_detail` longtext NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `sku` (`sku`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_produk`
--

LOCK TABLES `toko_produk` WRITE;
/*!40000 ALTER TABLE `toko_produk` DISABLE KEYS */;
INSERT INTO `toko_produk` VALUES (1,'MBG-SUV-HYD-001','Hyundai Palisade 2.2 Signature AT','Hyundai','SUV',2021,'AT',10000,'Putih','Makassar','DD 1234 BB',820000000,800000000,'B2C','ready',1,'100% Bebas Banjir & Bebas Bekas Tabrak Struktural.','mobil/hyundai_palisade_signature.jpg','SUV flagship premium dengan kenyamanan kelas atas, dilengkapi Captain Seat berbahan kulit premium dan panoramic sunroof. Unit sangat terawat, 100% lulus 150 titik inspeksi transparan MBG, serta siap pakai untuk area Makassar dan sekitarnya','Hyundai Palisade 2.2 Signature AT menghadirkan kombinasi sempurna antara kemewahan, ketangguhan mesin diesel turbo, dan kenyamanan keluarga modern. Unit ini telah melalui pemeriksaan menyeluruh oleh tim teknisi MBG dan dipastikan bebas dari riwayat bekas tabrak struktural maupun banjir.\r\n\r\nSpesifikasi Utama:\r\n\r\nMesin: 2.2L R CRDi Diesel Turbocharged (200 PS / 440 Nm)\r\n\r\nTransmisi: Otomatis 8-Speed Shift-by-Wire\r\n\r\nKabin: 7-Seater dengan Premium Captain Seats (Ventilated & Heated)\r\n\r\nFitur Keamanan: Hyundai SmartSense, Blind-Spot View Monitor, 6 Airbags\r\n\r\nFitur Kenyamanan: Dual Power Sunroof, Power Tailgate, Premium Audio\r\n\r\nJaminan Kepercayaan MBG Makassar:\r\n\r\n100% Lulus 150 Titik Inspeksi Transparan\r\n\r\nBebas Banjir & Bebas Bekas Tabrak Struktural\r\n\r\nDokumen & Pajak Lengkap (Siap Balik Nama)\r\n\r\nPilihan Layanan: Cek Unit di Showroom / Pengantaran Towing ke Rumah Area Makassar','2026-10-21 16:41:30.475710','2026-10-21 16:41:30.475780'),(2,'MBG-MPV-TYT-TEST-001','Toyota Avanza 1.3 G AT 2020','Toyota','MPV',2016,'AT',7000,'silver','Makassar','DD 1234 XX',165000000,159000000,'B2C','ready',1,'Data inspeksi belum diverifikasi. Kondisi mesin, bodi, interior, dokumen kendaraan, serta riwayat banjir dan tabrakan perlu diperiksa sebelum transaksi.','mobil/Silver_Toyota_Avanza_in_an_Outdoor_Lot.png','Toyota Avanza 1.3 G AT tahun 2020, MPV untuk kebutuhan keluarga dan aktivitas harian. Data ini merupakan contoh pengujian katalog MBG; kondisi aktual kendaraan belum diverifikasi.','Toyota Avanza 1.3 G AT 2020\r\n\r\nKendaraan kategori MPV yang ditujukan untuk kebutuhan keluarga dan mobilitas sehari-hari.\r\n\r\nData pengujian:\r\n- Merek: Toyota\r\n- Tahun: 2020\r\n- Transmisi: Otomatis\r\n- Jarak tempuh: 65.000 km (angka contoh)\r\n- Warna: Silver\r\n- Lokasi: Makassar\r\n\r\nHarga pada formulir merupakan nilai simulasi untuk pengujian aplikasi, bukan penawaran kendaraan nyata.\r\n\r\nSebelum kendaraan ditawarkan kepada pembeli, verifikasi kondisi mesin dan bodi, odometer, kelengkapan dokumen, pajak, serta riwayat banjir dan tabrakan.','2026-10-29 01:46:22.168377','2026-10-29 01:46:22.168441');
/*!40000 ALTER TABLE `toko_produk` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_titipjual`
--

DROP TABLE IF EXISTS `toko_titipjual`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_titipjual` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `nama_pemilik` varchar(100) NOT NULL,
  `no_whatsapp` varchar(20) NOT NULL,
  `email` varchar(254) DEFAULT NULL,
  `merek_model` varchar(150) NOT NULL,
  `tahun` int NOT NULL,
  `transmisi` varchar(20) NOT NULL,
  `harga_harapan` decimal(12,0) NOT NULL,
  `foto_kendaraan` varchar(100) NOT NULL,
  `catatan_kondisi` longtext,
  `status` varchar(20) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_titipjual`
--

LOCK TABLES `toko_titipjual` WRITE;
/*!40000 ALTER TABLE `toko_titipjual` DISABLE KEYS */;
INSERT INTO `toko_titipjual` VALUES (1,'Akmal','092190435876','akmallll.123456@gmail.com','Honda Jazz RS 2018',2019,'Manual',150000000,'titip_jual/Honda-Jazz-RS.jpg','Pajak mati dan kualitas ban bagian luarnya sudah tipis','disetujui','2026-10-22 03:00:30.092617'),(2,'Fawaz','0852409595536','akmallll.123456@gmail.com','Toyota Avanza All New',2022,'Otomatis (AT)',220000000,'titip_jual/New_Toyota_Avanza_Urban_Showcase.png','Data simulasi untuk pengujian, kondisi kendaraan dan dokumen belum diverifikasi.','disetujui','2026-10-29 01:51:27.033033');
/*!40000 ALTER TABLE `toko_titipjual` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `toko_transaksi`
--

DROP TABLE IF EXISTS `toko_transaksi`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `toko_transaksi` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `no_order` varchar(50) NOT NULL,
  `nominal_dp` decimal(12,0) NOT NULL,
  `metode_pembayaran` varchar(20) NOT NULL,
  `status_pembayaran` varchar(20) NOT NULL,
  `midtrans_order_id` varchar(100) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `booking_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `no_order` (`no_order`),
  UNIQUE KEY `booking_id` (`booking_id`),
  CONSTRAINT `toko_transaksi_booking_id_e3552296_fk` FOREIGN KEY (`booking_id`) REFERENCES `toko_bookingcekunit` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `toko_transaksi`
--

LOCK TABLES `toko_transaksi` WRITE;
/*!40000 ALTER TABLE `toko_transaksi` DISABLE KEYS */;
/*!40000 ALTER TABLE `toko_transaksi` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-10-29  8:42:47
